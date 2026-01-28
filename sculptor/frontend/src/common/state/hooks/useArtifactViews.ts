import { useAtom, useAtomValue } from "jotai";
import { useEffect } from "react";

import {
  defaultEnabledArtifactViewIds,
  registeredArtifactViews,
} from "../../../pages/chat/components/artifacts/Registry";
import type { ArtifactView } from "../../../pages/chat/Types.ts";
import { enabledArtifactViewsAtom } from "../atoms/artifactViews";
import { globalDevModeAtom } from "../atoms/devMode.ts";
import { sculptorSettingsAtom } from "../atoms/sculptorSettings.ts";
import { areDevSuggestionsOnAtom, areSuggestionsEnabledAtom, isScoutBetaFeatureOnAtom } from "../atoms/userConfig.ts";

/**
 * Hook for getting enabled artifact views sorted by tab order
 */
export const useArtifactViewsByTabOrder = (): ReadonlyArray<ArtifactView> => {
  const [enabledArtifactViews, setEnabledArtifactViews] = useAtom(enabledArtifactViewsAtom);
  const settings = useAtomValue(sculptorSettingsAtom);
  const areSuggestionsEnabled = useAtomValue(areSuggestionsEnabledAtom);
  const areDevSuggestionsOn = useAtomValue(areDevSuggestionsOnAtom);
  const isScoutBetaFeatureOn = useAtomValue(isScoutBetaFeatureOnAtom);
  const isDevMode = useAtomValue(globalDevModeAtom);

  useEffect(() => {
    // Start with default enabled views
    const enabledIds = new Set(defaultEnabledArtifactViewIds);
    if (settings?.ENABLED_FRONTEND_ARTIFACT_VIEWS) {
      const additionalIds = settings.ENABLED_FRONTEND_ARTIFACT_VIEWS.split(",").map((v) => v.trim());
      additionalIds.forEach((id) => enabledIds.add(id));
    }

    let views = registeredArtifactViews.filter((v) => enabledIds.has(v.id));

    const areSuggestionsDisabled = !areSuggestionsEnabled;
    const isChecksDisabled = areSuggestionsDisabled || !isDevMode;

    const viewsToHide: Array<string> = [];
    if (areSuggestionsDisabled) {
      viewsToHide.push("Suggestions");
    }

    if (isChecksDisabled) {
      viewsToHide.push("Checks");
    }

    if (viewsToHide.length > 0) {
      views = views.filter((v) => !viewsToHide.includes(v.id));
    }

    if (!isDevMode || !areSuggestionsEnabled) {
      views = views.filter((v) => v.id !== "Checks");
    }

    // only show dev suggestions view if dev mode is on and it's enabled
    if (!isDevMode || !areDevSuggestionsOn) {
      views = views.filter((v) => v.id !== "DevSuggestions");
    }

    if (!isDevMode || !isScoutBetaFeatureOn) {
      views = views.filter((v) => v.id !== "Scout");
      views = views.filter((v) => v.id !== "DevScout");
    }

    views = views.sort((a, b) => a.tabOrder - b.tabOrder);
    setEnabledArtifactViews(views);
  }, [settings, areSuggestionsEnabled, areDevSuggestionsOn, isDevMode, isScoutBetaFeatureOn, setEnabledArtifactViews]);

  return enabledArtifactViews;
};
