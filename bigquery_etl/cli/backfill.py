"""bigquery-etl CLI backfill command."""

import re
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

import click
import yaml

from ..backfill.parse import (
    BACKFILL_FILE,
    DEFAULT_REASON,
    DEFAULT_WATCHER,
    Backfill,
    BackfillStatus,
)
from ..backfill.utils import get_backfill_file_to_entries_map
from ..backfill.validate import (
    validate_duplicate_entry_dates,
    validate_file,
    validate_overlap_dates,
)
from ..cli.utils import paths_matching_name_pattern, project_id_option, sql_dir_option
from ..util import extract_from_query_path

QUALIFIED_TABLE_NAME_RE = re.compile(
    r"(?P<project_id>[a-zA-z0-9_-]+)\.(?P<dataset_id>[a-zA-z0-9_-]+)\.(?P<table_id>[a-zA-z0-9_-]+)"
)


# todo: refractor create & validate commands to use backfills_matching_name_pattern method
@click.group(help="Commands for managing backfills.")
@click.pass_context
def backfill(ctx):
    """Create the CLI group for the backfill command."""
    # create temporary directory generated content is written to
    # the directory will be deleted automatically after the command exits
    ctx.ensure_object(dict)
    ctx.obj["TMP_DIR"] = ctx.with_resource(tempfile.TemporaryDirectory())


@backfill.command(
    help="""Create a new backfill entry in the backfill.yaml file.  Create
    a backfill.yaml file if it does not already exist.

    Examples:

    \b
    ./bqetl backfill create moz-fx-data-shared-prod.telemetry_derived.deviations_v1 \\
      --start_date=2021-03-01 \\
      --end_date=2021-03-31 \\
      --exclude=2021-03-03 \\
    """,
)
@click.argument("qualified_table_name")
@sql_dir_option
@click.option(
    "--start_date",
    "--start-date",
    "-s",
    help="First date to be backfilled. Date format: yyyy-mm-dd",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    required=True,
)
@click.option(
    "--end_date",
    "--end-date",
    "-e",
    help="Last date to be backfilled. Date format: yyyy-mm-dd",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=datetime.today(),
)
@click.option(
    "--exclude",
    "-x",
    multiple=True,
    help="Dates excluded from backfill. Date format: yyyy-mm-dd",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
)
@click.option(
    "--watcher",
    "-w",
    help="Watcher of the backfill (email address)",
    default=DEFAULT_WATCHER,
)
@click.pass_context
def create(
    ctx,
    qualified_table_name,
    sql_dir,
    start_date,
    end_date,
    exclude,
    watcher,
):
    """CLI command for creating a new backfill entry in backfill.yaml file.

    A backfill.yaml file will be created if it does not already exist.
    """
    try:
        match = QUALIFIED_TABLE_NAME_RE.match(qualified_table_name)
        project_id = match.group("project_id")
        dataset_id = match.group("dataset_id")
        table_id = match.group("table_id")
    except AttributeError:
        click.echo(
            "Qualified table name must be named like:" + " <project>.<dataset>.<table>"
        )
        sys.exit(1)

    path = Path(sql_dir)

    query_path = path / project_id / dataset_id / table_id

    if not query_path.exists():
        click.echo(f"{project_id}.{dataset_id}.{table_id}" + " does not exist")
        sys.exit(1)

    backfill = Backfill(
        entry_date=date.today(),
        start_date=start_date.date(),
        end_date=end_date.date(),
        excluded_dates=[e.date() for e in list(exclude)],
        reason=DEFAULT_REASON,
        watchers=[watcher],
        status=BackfillStatus.DRAFTING,
    )

    backfills = []

    backfill_file = query_path / BACKFILL_FILE

    if backfill_file.exists():
        backfills = Backfill.entries_from_file(backfill_file)
        for entry in backfills:
            validate_duplicate_entry_dates(backfill, entry)
            if entry.status == BackfillStatus.DRAFTING:
                validate_overlap_dates(backfill, entry)

    backfills.insert(0, backfill)

    backfill_file.write_text(
        "\n".join(backfill.to_yaml() for backfill in sorted(backfills, reverse=True))
    )

    click.echo(f"Created backfill entry in {backfill_file}")


@backfill.command(
    help="""Validate backfill.yaml file format and content.

    Examples:

    ./bqetl backfill validate moz-fx-data-shared-prod.telemetry_derived.clients_daily_v6

    \b
    # validate all backfill.yaml files if table is not specified
    Use the `--project_id` option to change the project to be validated;
    default is `moz-fx-data-shared-prod`.

    Examples:

    ./bqetl backfill validate
    """
)
@click.argument("qualified_table_name", required=False)
@sql_dir_option
@project_id_option("moz-fx-data-shared-prod")
@click.pass_context
def validate(
    ctx,
    qualified_table_name,
    sql_dir,
    project_id,
):
    """Validate backfill.yaml files."""
    backfill_files = []

    if qualified_table_name:
        try:
            match = QUALIFIED_TABLE_NAME_RE.match(qualified_table_name)
            project_id = match.group("project_id")
            dataset_id = match.group("dataset_id")
            table_id = match.group("table_id")
        except AttributeError:
            click.echo(
                "Qualified table name must be named like:"
                + " <project>.<dataset>.<table>"
            )
            sys.exit(1)

        path = Path(sql_dir)
        query_path = path / project_id / dataset_id / table_id

        if not query_path.exists():
            click.echo(f"{project_id}.{dataset_id}.{table_id}" + " does not exist")
            sys.exit(1)

        backfill_file = path / project_id / dataset_id / table_id / BACKFILL_FILE
        backfill_files.append(backfill_file)

    else:
        backfill_files = paths_matching_name_pattern(
            None, sql_dir, project_id, [BACKFILL_FILE]
        )

    for file in backfill_files:
        try:
            validate_file(file)
        except (yaml.YAMLError, ValueError) as e:
            click.echo(f"{file} contains the following error:\n {e}")
            sys.exit(1)

    if qualified_table_name:
        click.echo(
            f"{BACKFILL_FILE} has been validated for {project_id}.{dataset_id}.{table_id} "
        )
    elif backfill_files:
        click.echo(
            f"All {BACKFILL_FILE} files have been validated for project {project_id}"
        )


@backfill.command(
    help="""Get backfill(s) information from all or specific table(s).

    Examples:

    # Get info for specific table.
    ./bqetl backfill info moz-fx-data-shared-prod.telemetry_derived.clients_daily_v6

    \b
    # Get info for all tables.
    ./bqetl backfill info

    \b
    # Get info from all tables with specific status.
    ./bqetl backfill info --status=Drafting
    """,
)
@click.argument("qualified_table_name", required=False)
@sql_dir_option
@project_id_option("moz-fx-data-shared-prod")
@click.option(
    "--status",
    type=click.Choice([s.value.lower() for s in BackfillStatus]),
    help="Filter backfills with this status.",
)
@click.pass_context
def info(ctx, qualified_table_name, sql_dir, project_id, status):
    """Return backfill(s) information from all or specific table(s)."""
    backfills = get_backfill_file_to_entries_map(
        sql_dir, project_id, qualified_table_name
    )

    total_backfills_count = 0

    for file, entries in backfills.items():
        if status is not None:
            entries = [e for e in entries if e.status.value.lower() == status.lower()]

        entries_count = len(entries)

        if entries_count:
            total_backfills_count += entries_count

            project, dataset, table = extract_from_query_path(file)

            status_str = f" with {status} status" if status is not None else ""
            click.echo(
                f"""{project}.{dataset}.{table} has {entries_count} backfill(s){status_str}:"""
            )

            for entry in entries:
                click.echo(str(entry))

    click.echo(f"\nThere are a total of {total_backfills_count} backfill(s)")
