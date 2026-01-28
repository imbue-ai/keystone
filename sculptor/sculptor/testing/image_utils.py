"""Test utilities for working with Docker images."""

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.concurrency_group import ConcurrencyGroup
from sculptor.database.core import create_new_engine
from sculptor.database.utils import convert_sqlite_url_to_read_only_format
from sculptor.services.data_model_service.sql_implementation import SQLDataModelService


def get_project_id_for_task(database_url: str, task_id: str, concurrency_group: ConcurrencyGroup) -> ProjectID:
    # Set up database connection
    data_model_service = SQLDataModelService(concurrency_group=concurrency_group)
    database_url_read_only = convert_sqlite_url_to_read_only_format(database_url)
    data_model_service._engine = create_new_engine(database_url_read_only)
    data_model_service._is_read_only = True
    data_model_service.start()

    try:
        with data_model_service.open_task_transaction() as transaction:
            # Get the task
            task = transaction.get_task(task_id)
            if not task:
                raise Exception("Task {} not found", task_id)
            return task.project_id

    finally:
        data_model_service.stop()
