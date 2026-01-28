from pathlib import Path
from typing import Final
from typing import Literal
from typing import Protocol
from typing import TypeVar
from typing import cast
from typing import runtime_checkable

from loguru import logger
from pydantic import computed_field

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.async_monkey_patches import log_exception
from imbue_core.constants import ExceptionPriority
from imbue_core.itertools import first
from imbue_core.itertools import generate_chunks
from imbue_core.pydantic_serialization import MutableModel
from imbue_core.pydantic_serialization import SerializableModel
from imbue_core.pydantic_serialization import model_dump_json
from imbue_core.pydantic_serialization import model_load_json
from sculptor.services.data_model_service.data_types import DataModelTransaction
from sculptor.services.data_model_service.data_types import Project
from sculptor.services.git_repo_service.api import GitRepoService
from sculptor.services.git_repo_service.default_implementation import LocalReadOnlyGitRepo
from sculptor.services.git_repo_service.default_implementation import LocalWritableGitRepo
from sculptor.services.git_repo_service.default_implementation import RemoteWritableGitRepo
from sculptor.services.git_repo_service.error_types import CommitRef
from sculptor.services.git_repo_service.error_types import GitRepoError
from sculptor.services.git_repo_service.error_types import GitStashApplyError
from sculptor.services.git_repo_service.error_types import StashApplyEndState
from sculptor.services.git_repo_service.git_repos import AbsoluteGitPosition
from sculptor.services.git_repo_service.git_repos import ReadOnlyGitRepo
from sculptor.services.git_repo_service.git_repos import WritableGitRepo
from sculptor.utils.build import get_sculptor_folder
from sculptor.utils.timeout import log_runtime
from sculptor.utils.timeout import log_runtime_decorator

StrTupleT = TypeVar("StrTupleT", bound=tuple[str, ...])

_SCULPTOR_REFS_NAMESPACE_ROOT_PREFIX: Final = "refs/sculptor"

_SCULPTOR_STASH_SUBJECT: Final = "Sculptor stash before pairing mode transition"
_SCULPTOR_TRANSITION_TRAILER_KEY: Final = "Sculptor-transition-json"

# Technically, another instance or a manual action can mutate our state.
# Thus we say "no managed singleton" to indicate:
# 1. We (should) always check across projects before mutating
# 2. We should avoid hard assertions on singleton-consistency
_NoManagedSingletonMarker = Literal["__NO_MANAGED_SINGLETON_MARKER__"]
__NO_MANAGED_SINGLETON_MARKER__: Final[_NoManagedSingletonMarker] = "__NO_MANAGED_SINGLETON_MARKER__"


# keep as function for mocking / env changes
def _get_stash_owning_project_marker() -> Path:
    return get_sculptor_folder() / "stash_singleton_owning_project.txt"


def _overwrite_marker_file(content: str) -> None:
    marker = _get_stash_owning_project_marker()
    marker.unlink(missing_ok=True)
    marker.touch()
    marker.write_text(content)


def _write_stash_owning_project_marker(project_id: ProjectID, stash: "SculptorStash") -> None:
    _overwrite_marker_file(str(project_id))
    logger.debug("Wrote to {} for project {}: {}", _get_stash_owning_project_marker(), project_id, stash)


def _get_stash_owning_project() -> ProjectID | _NoManagedSingletonMarker | None:
    marker_file = _get_stash_owning_project_marker()
    if not marker_file.exists():
        return None
    content = marker_file.read_text()
    if content == __NO_MANAGED_SINGLETON_MARKER__:
        return __NO_MANAGED_SINGLETON_MARKER__
    try:
        return ProjectID(content)
    except Exception:
        marker_file.unlink(missing_ok=True)
        return None


# TODO: Bad name... sculptor git transition?
class AbsoluteGitTransition(SerializableModel):
    from_position: AbsoluteGitPosition
    to_branch: str


@runtime_checkable
class _RunGitProtocol(Protocol):
    def _run_git(self, args: list[str]) -> str: ...


# TODO: add a MissingSculptorStash to handle the case where the ref is messed with externally
class SculptorStash(SerializableModel):
    # Transition this stash was made to enable
    enabled_transition: AbsoluteGitTransition
    absolute_stash_ref: str

    @computed_field
    @property
    def source_branch(self) -> str:
        return self.enabled_transition.from_position.branch

    @property
    def fallback_temporary_branch_name(self) -> str:
        return f"_sculptor/{self.enabled_transition.from_position.ref_safe_identifier}"


class SculptorStashSingleton(SerializableModel):
    stash: SculptorStash
    owning_project_id: ProjectID


class SculptorStashReader(MutableModel):
    # TODO typing a bit awkward
    repo: _RunGitProtocol

    def _run_git(self, args: list[str]) -> str:
        return self.repo._run_git(args)

    @property
    def full_namespace(self) -> str:
        return f"{_SCULPTOR_REFS_NAMESPACE_ROOT_PREFIX}/stash"

    def _validate_ref(self, relative_ref_name: str) -> None:
        assert relative_ref_name.startswith(self.full_namespace), (
            f"logic error: {self.full_namespace} qualified refs should be used after _write_ref"
        )

    def _write_ref(self, relative_ref_name: str, commit_hash: str) -> str:
        """Write a git ref in this namespace pointing to the given commit hash."""
        assert not relative_ref_name.startswith(self.full_namespace), (
            f"logic error: relative ref name expected, got full namespace {self.full_namespace}"
        )
        ref = f"{self.full_namespace}/{relative_ref_name}"
        self._run_git(["update-ref", ref, commit_hash])
        return ref

    # TODO(mjr) I ended up with this after realizing for-each-ref can yield all we need,
    def _read_ref_formatted_fields_from_refs_in_namespace(self, fields: StrTupleT) -> tuple[StrTupleT, ...]:
        null_interpolate = "%00"
        null = "\x00"
        format = null_interpolate.join(f"%({field})" for field in fields) + null_interpolate
        output = self._run_git(["for-each-ref", f"--format={format}", self.full_namespace])
        grouped_fields = generate_chunks(output.split(null), len(fields))
        return cast(tuple[StrTupleT, ...], tuple(f for f in grouped_fields if len(f) == len(fields)))

    def _parse_stash(self, ref_fields: tuple[str, str, str, str, str]) -> SculptorStash:
        # logger.trace("attempting to parse ref fields: {}", ref_fields)
        absolute_stash_ref, trailer_with_our_json, *read_for_debugging = ref_fields
        transition = model_load_json(AbsoluteGitTransition, trailer_with_our_json.strip())
        return SculptorStash(enabled_transition=transition, absolute_stash_ref=absolute_stash_ref.strip())

    def get_stashes(self) -> tuple[SculptorStash, ...]:
        metadata_trailer_value = f"trailers:key={_SCULPTOR_TRANSITION_TRAILER_KEY},valueonly=true"
        ref_fields = self._read_ref_formatted_fields_from_refs_in_namespace(
            ("refname", metadata_trailer_value, "objectname", "contents:subject", "contents:body")
        )
        return tuple(self._parse_stash(fields) for fields in ref_fields)

    @property
    def is_stash_present(self) -> bool:
        return len(self.get_stashes()) > 0

    # unused atm as everything goes through the singleton api
    def maybe_get_stash_by_ref(self, absolute_stash_ref: str) -> SculptorStash | None:
        stashes = self.get_stashes()
        return first(s for s in stashes if s.absolute_stash_ref == absolute_stash_ref)

    def maybe_get_singleton_stash(self) -> SculptorStash | None:
        stashes = self.get_stashes()
        if len(stashes) == 0:
            return None
        try:
            assert len(stashes) == 1, (
                f"Expected exactly one sculptor stash given singleton usage, found {len(stashes)}"
            )
        except AssertionError as e:
            message = (
                "Expected exactly one sculptor stash given singleton usage, found {count} ({stashes}).",
                "This indicates a bug - please contact support.",
            )
            log_exception(e, " ".join(message), ExceptionPriority.HIGH_PRIORITY, count=len(stashes), stashes=stashes)
            raise
        return stashes[0]


# Made private to prevent non-singleton usage - use top-level functions instead:
# - checkout_branch_maybe_stashing_as_we_go
# - pop_namespaced_stash_into_source_branch
class _SculptorStasher(SculptorStashReader):
    repo: LocalWritableGitRepo | RemoteWritableGitRepo

    def delete_ref(self, absolute_ref_name: str, is_missing_ok: bool = False) -> bool:
        """Delete a git ref in this namespace."""
        self._validate_ref(absolute_ref_name)
        try:
            self._run_git(["update-ref", "-d", absolute_ref_name])
            return True
        except GitRepoError as e:
            is_missing = "refused to delete the ref" in str(e)
            if is_missing and is_missing_ok:
                return False
            raise

    def _get_expected_transition(self, target_branch: str) -> AbsoluteGitTransition:
        return AbsoluteGitTransition(
            from_position=self.repo.get_current_absolute_git_position(),
            to_branch=target_branch,
        )

    # TODO would be nice to && these initial commands, would have to parse "No local changes" in the bash pipeline though
    def _push_sculptor_stash_before_transition(
        self, expected_transition: AbsoluteGitTransition, is_untracked_included: bool
    ) -> bool:
        """Create a git stash and return whether any changes were stashed."""
        # Annoying: stash create doesn't have options https://git-scm.com/docs/git-stash
        args = ["stash", "push", "--no-keep-index"]
        if is_untracked_included:
            args.append("--include-untracked")
        trailer_key_value = f"{_SCULPTOR_TRANSITION_TRAILER_KEY}: {model_dump_json(expected_transition)}"
        args.extend(["--message", f"{_SCULPTOR_STASH_SUBJECT}\n\n{trailer_key_value}"])
        result = self._run_git(args)
        # TODO: Verify this isn't version dependent
        return "No local changes" not in result

    def _move_stash_into_sculptor_namespace(self, stashed_position: AbsoluteGitPosition) -> str:
        # TODO VERIFY stash@{0} IS WHAT WE THINK IT IS (PARSE)

        # Just assume nobody is racing us for now - I mean come on
        # as far as I know we would have to inspect the message to verify
        #
        # Actual `git stash export` was insanely slow on generally_intelligent,
        # so we do this and have a more complex import.
        #
        # I _believe_ it should have all the same properties as the stash export within the same repo.
        absolute_ref = self._write_ref(stashed_position.ref_safe_identifier, "stash@{0}")
        self._run_git(["stash", "drop"])
        return absolute_ref

    def _create_namespaced_stash_before_transition(
        self, expected_transition: AbsoluteGitTransition
    ) -> SculptorStash | None:
        with log_runtime("LOCAL_SYNC.stash._create_git_stash_before_transition"):
            is_git_stash_created = self._push_sculptor_stash_before_transition(
                expected_transition, is_untracked_included=True
            )
        if not is_git_stash_created:
            return None
        with log_runtime("LOCAL_SYNC.stash._pop_stash_top_into_ref_for"):
            sculptor_stash_ref = self._move_stash_into_sculptor_namespace(expected_transition.from_position)
        return SculptorStash(
            enabled_transition=expected_transition,
            absolute_stash_ref=sculptor_stash_ref,
        )

    def _verify_transition_safety(self, target_branch: str, op: str, is_full_safety_needed: bool) -> None:
        status = self.repo.get_current_status()

        if is_full_safety_needed:
            is_safe = status.is_clean_and_safe_to_operate_on
        else:
            is_safe = not status.is_in_intermediate_state

        if is_safe:
            return

        # TODO should be new error type, probably
        raise GitRepoError(
            f"Cannot {op} while repository is in an intermediate state ({status.describe()}, {target_branch=})",
            operation=op,
            branch_name=self.repo.get_current_git_branch(),
            repo_url=self.repo.get_repo_url(),
        )

    # NOTE: May seem odd to bundle this all up but this way we can save the transition info in the stash itself for better interpretability
    def checkout_branch_stashing_as_we_go(self, target_branch: str) -> SculptorStash | None:
        self._verify_transition_safety(target_branch, "checkout_branch_stashing_as_we_go", is_full_safety_needed=False)
        expected_transition = self._get_expected_transition(target_branch)
        stash = self._create_namespaced_stash_before_transition(expected_transition)
        if self.repo.get_current_git_branch() == target_branch:
            return stash
        try:
            self.repo.git_checkout_branch(target_branch)
        except Exception as e:
            # TODO: not sure
            log_exception(
                e,
                "Unexpected error during branch checkout. Leaving behind {stash} as clearly the git state is funky",
                priority=ExceptionPriority.MEDIUM_PRIORITY,
                stash=stash.absolute_stash_ref if stash else None,
            )
        return stash

    def _fallback_stash_apply_via_temporary_branch(self, stash: SculptorStash, commit_ref: CommitRef) -> None:
        """In the event of a conflict, we can restore via a janky merge flow.

        I know this method looks obscene but without the error handling it is just:
        # branch and commit so we can merge
        git stash branch <fallback_temporary_branch_name> <absolute_stash_ref>
        git commit -am "restoring stash $absolute_stash_ref"
        # merge into whatever without committing the result, leaving any conflicts
        git checkout <source_branch>
        git merge --no-commit <fallback_temporary_branch_name>
        git branch -D <fallback_temporary_branch_name>
        git update-ref -d <absolute_stash_ref>
        """
        # TODO ADD POSTHOG FOR THIS
        fallback_branch = stash.fallback_temporary_branch_name
        # pain https://stackoverflow.com/a/51276389
        try:
            # TODO Check behavior when intermediate repo state exists and add git reset if needed
            self._run_git(["stash", "branch", fallback_branch, stash.absolute_stash_ref])
        except Exception as e:
            err = "failed to apply sculptor stash: couldn't even `stash branch` for fallback"
            raise GitStashApplyError(err, commit_ref, StashApplyEndState.MERGE_FAILURE) from e

        try:
            self._run_git(["add", "-A"])
            self._run_git(["commit", "-m", f"restoring stash {stash.absolute_stash_ref}"])
            self.repo.git_checkout_branch(stash.source_branch)
            self._run_git(["merge", "--no-commit", fallback_branch])
        except Exception as e:
            try:
                self._run_git(["branch", "-D", fallback_branch])
            except Exception as cleanup_e:
                err = f"total stash apply failure: merge fallback failed, and couldn't clean up fallback branch {fallback_branch}"
                raise GitStashApplyError(err, commit_ref, StashApplyEndState.TOTAL_FAILURE) from cleanup_e
            err = "failed to apply sculptor stash: merge fallback failed"
            raise GitStashApplyError(err, commit_ref, StashApplyEndState.MERGE_FAILURE) from e

        self.delete_ref(stash.absolute_stash_ref)
        self._run_git(["branch", "-D", fallback_branch])

    def pop_namespaced_stash_into_source_branch(self, stash: SculptorStash) -> None:
        self._validate_ref(stash.absolute_stash_ref)
        self._verify_transition_safety(
            stash.source_branch, "sculptor_checkout_and_pop_stash", is_full_safety_needed=True
        )
        commit_ref = CommitRef(
            ref_name=stash.absolute_stash_ref,
            # TODO: This is raises a GitRepoError if the ref is missing.
            # currently try and pre-empt elsewhere, but maybe should have dedicated exception
            commit_hash=self.repo.get_branch_head_commit_hash(stash.absolute_stash_ref),
        )
        self.repo.git_checkout_branch(stash.source_branch)
        try:
            self._run_git(["stash", "apply", "--index", stash.absolute_stash_ref])
            self.delete_ref(stash.absolute_stash_ref)
        except Exception as e:
            err = "conflict during initial `stash apply --index`"
            log_exception(
                e,
                "LOCAL_SYNC: {err} for {ref}. Attempting fallback",
                ExceptionPriority.LOW_PRIORITY,
                err=err,
                ref=commit_ref,
            )
            self._fallback_stash_apply_via_temporary_branch(stash, commit_ref)
            final_message = f"merged stash via fallback after {err}"
            log_exception(e, "LOCAL_SYNC: {msg}", ExceptionPriority.LOW_PRIORITY, msg=final_message)
            raise GitStashApplyError(final_message, commit_ref, StashApplyEndState.MERGE_FALlBACK_SUCCESS) from e


def build_sculptor_stash_reader(repo: ReadOnlyGitRepo) -> SculptorStashReader:
    assert isinstance(repo, LocalReadOnlyGitRepo)
    return SculptorStashReader(repo=repo)


def _build_sculptor_stasher(repo: WritableGitRepo) -> _SculptorStasher:
    assert isinstance(repo, (LocalWritableGitRepo, RemoteWritableGitRepo))
    return _SculptorStasher(repo=repo)


def checkout_branch_maybe_stashing_as_we_go(
    project_id: ProjectID, repo: WritableGitRepo, target_branch: str
) -> SculptorStashSingleton | None:
    stasher = _build_sculptor_stasher(repo)
    # just added safety - shouldn't be necessary but to assume as such would be hubris
    assert not stasher.is_stash_present, (
        f"Shouldn't have gotten here due to singleton usage ({stasher.get_stashes()=})"
    )
    stash = stasher.checkout_branch_stashing_as_we_go(target_branch)
    if not stash:
        return None
    _write_stash_owning_project_marker(project_id, stash)
    return SculptorStashSingleton(stash=stash, owning_project_id=project_id)


@log_runtime_decorator("LOCAL_SYNC.stash.pop_namespaced_stash_into_source_branch")
def pop_namespaced_stash_into_source_branch(
    project_id: ProjectID, repo: WritableGitRepo, stash: SculptorStash
) -> None:
    owner = _get_stash_owning_project()

    is_owned_by_singleton = isinstance(owner, ProjectID) and owner == project_id
    if not is_owned_by_singleton:
        # This implies multiple stashes got created or the owner singleton got corrupted.
        # Regardless, popping a different stash should still be valid.
        #
        # we'll just try our best and leave the existing ownership indicator alone
        logger.error("Proceeding to pop stash in {} despite singleton owned by {}. stash={}", project_id, owner, stash)

    # TODO: 2x check we're validating this stash exists before rewinding from local sync
    _build_sculptor_stasher(repo).pop_namespaced_stash_into_source_branch(stash)

    if is_owned_by_singleton:
        # don't want to unlink until after successful pop
        _overwrite_marker_file(__NO_MANAGED_SINGLETON_MARKER__)


def delete_namespaced_stash_in_project(project_id: ProjectID, repo: WritableGitRepo, stash: SculptorStash) -> bool:
    "Returns True if stash was deleted, False if it was missing."
    stasher = _build_sculptor_stasher(repo)
    is_deleted = stasher.delete_ref(stash.absolute_stash_ref, is_missing_ok=True)

    owner = _get_stash_owning_project()

    if owner == project_id:
        _overwrite_marker_file(__NO_MANAGED_SINGLETON_MARKER__)
    else:
        msg = "delete_namespaced_stash_in_project inconsistency: {} != owner {} (is_deleted={})"
        logger.info(msg, project_id, owner, is_deleted)

    return is_deleted


def _maybe_read_stash_singleton_for_project(
    repo_service: GitRepoService, project: Project
) -> SculptorStashSingleton | None:
    with repo_service.open_local_user_git_repo_for_read(project) as repo:
        stashes = build_sculptor_stash_reader(repo).get_stashes()
    if len(stashes) == 0:
        return None
    assert len(stashes) == 1, f"Expected exactly one sculptor stash given singleton usage, found {len(stashes)}"
    stash = stashes[0]
    return SculptorStashSingleton(stash=stash, owning_project_id=project.object_id)


def _find_and_mark_unmarked_stash(
    transaction: DataModelTransaction, repo_service: GitRepoService
) -> tuple[SculptorStashSingleton, Project] | None:
    for p in transaction.get_projects():
        stash = _maybe_read_stash_singleton_for_project(repo_service, p)
        if stash is not None:
            _write_stash_owning_project_marker(p.object_id, stash.stash)
            return stash, p
    _overwrite_marker_file(__NO_MANAGED_SINGLETON_MARKER__)
    return None


# TODO(mjr): Big assumption violation -- if the same repo is pointed to by multiple projects there is a mismatch of expectations.
def read_global_stash_singleton_if_present(
    repo_service: GitRepoService, transaction: DataModelTransaction, is_strict: bool = True
) -> tuple[SculptorStashSingleton, Project] | None:
    project_id = _get_stash_owning_project()

    # might as well be super confident before we go into a LocalSync transition
    if (project_id is None) or project_id == __NO_MANAGED_SINGLETON_MARKER__:
        if is_strict or project_id is None:
            return _find_and_mark_unmarked_stash(transaction, repo_service)
        # We've intentionally marked the no owner, so now we only check on a new local sync
        return None

    project = transaction.get_project(project_id)
    if project is None:
        logger.error("strange state: stash owner {} missing. Will unlink and attempt to replace", project_id)
        return _find_and_mark_unmarked_stash(transaction, repo_service)

    stash = _maybe_read_stash_singleton_for_project(repo_service, project)
    if stash is None:
        logger.error("{} says {} has stash but can't find. Unlinking", _get_stash_owning_project_marker(), project_id)
        _get_stash_owning_project_marker().unlink(missing_ok=True)
        return None
    return stash, project


# TODO would be better to always get full stash in case it is manually deleted
def is_global_stash_singleton_stashed() -> bool:
    return isinstance(_get_stash_owning_project(), ProjectID)
