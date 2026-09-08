"""Typer CLI: analyst run | brief | company | spend | audit | eval | rate."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from .config import ConfigError, Settings, load_settings, load_settings_no_llm

if TYPE_CHECKING:
    from .company import CompanyFiles
    from .db import Store
from .logging_setup import setup_logging

app = typer.Typer(help="Claude-analyst: investment research over the announcement lake")
company_app = typer.Typer(help="Per-company files")
app.add_typer(company_app, name="company")

log = logging.getLogger("analyst")


def _settings(need_llm: bool) -> Settings:
    try:
        return load_settings() if need_llm else load_settings_no_llm()
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc


def _open_all(settings: Settings, need_llm: bool) -> tuple:  # type: ignore[type-arg]
    from .adapter import LakeAdapter, LakeNotFound
    from .company import CompanyFiles
    from .db import Store
    from .router import Router

    store = Store(settings.db_path)
    try:
        lake = LakeAdapter.from_settings(settings)
    except LakeNotFound as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc
    router = Router.load(settings.routing_yaml)
    companies = CompanyFiles(settings.companies_dir)
    llm = None
    if need_llm:
        from .llm import LLMClient

        llm = LLMClient(settings, store)
    return store, lake, router, companies, llm


def _print_spend(store: Store, settings: Settings) -> None:
    total = store.total_spend_usd()
    today = store.spend_usd_on_day(datetime.utcnow().strftime("%Y-%m-%d"))
    typer.echo(
        f"API spend: ${today:.4f} today (cap ${settings.budget_usd_daily:.2f}), "
        f"${total:.4f} total (cap ${settings.budget_usd_total:.2f})"
    )


@app.command()
def run(
    since: str = typer.Option(None, help="Process partitions from this date (YYYY-MM-DD)"),
    until: str = typer.Option(None, help="Last partition date (default: today)"),
    limit: int = typer.Option(None, help="Stop after N announcements (cheap partial runs)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="No model calls; report routings"),
    realtime: bool = typer.Option(
        False, "--realtime", help="Per-call API instead of the Batch API"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    plain_logs: bool = typer.Option(False, "--plain-logs", help="Human-readable logs"),
) -> None:
    """Process new announcements from the lake through the full pipeline."""
    setup_logging(verbose=verbose, json_logs=not plain_logs)
    settings = _settings(need_llm=not dry_run)
    store, lake, router, companies, llm = _open_all(settings, need_llm=not dry_run)

    from .pipeline import Pipeline

    if since:
        since_dt = datetime.fromisoformat(since)
    else:
        latest = lake.latest_complete_day()
        if latest is None:
            typer.secho("No complete day found in the lake in the last 14 days.", fg="red", err=True)
            raise typer.Exit(1)
        since_dt = datetime.fromisoformat(latest)
    until_dt = datetime.fromisoformat(until) if until else None

    pipeline = Pipeline(settings, store, lake, router, companies, llm, dry_run=dry_run)
    report = pipeline.run(since_dt, until_dt, limit=limit, realtime=realtime)
    typer.echo(report.summary())
    _print_spend(store, settings)
    if report.budget_stopped:
        raise typer.Exit(3)


@app.command()
def brief(
    date: str = typer.Option(None, "--date", help="Brief date YYYY-MM-DD (default: latest run day)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Write data/briefs/YYYY-MM-DD.md for the day."""
    setup_logging(verbose=verbose)
    settings = _settings(need_llm=False)
    from .brief import build_brief
    from .db import Store

    store = Store(settings.db_path)
    day = date or datetime.now(settings.timezone).strftime("%Y-%m-%d")
    path = build_brief(store, day, settings.briefs_dir, settings.budget_usd_total)
    typer.echo(f"Brief written: {path}")
    _print_spend(store, settings)


@app.command()
def spend() -> None:
    """Show cumulative and per-day API spend."""
    setup_logging()
    settings = _settings(need_llm=False)
    from .db import Store

    store = Store(settings.db_path)
    for row in store.spend_breakdown():
        typer.echo(
            f"{row['day']} {row['model']:<22} {row['purpose']:<8} calls={row['calls']:<4} "
            f"in={row['input_tokens']:<8} out={row['output_tokens']:<7} "
            f"cache_read={row['cache_read_tokens']:<8} ${row['cost_usd']:.4f}"
        )
    _print_spend(store, settings)


@app.command()
def audit(
    date: str = typer.Option(None, "--date", help="Day to audit (default: yesterday UTC)"),
    realtime: bool = typer.Option(
        False, "--realtime", help="Per-call API instead of the Batch API"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run the strong-model audit sampler over culled/low-scored announcements."""
    setup_logging(verbose=verbose)
    settings = _settings(need_llm=True)
    store, lake, _router, _companies, llm = _open_all(settings, need_llm=True)
    from .audit import run_audit
    from .llm import BudgetExceeded
    from .triage.prompts import load_taxonomy

    taxonomy = load_taxonomy(settings.taxonomy_yaml)
    day = date or (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    assert llm is not None
    try:
        counts = run_audit(
            store,
            lake,
            llm,
            day,
            sample_rate=float(taxonomy.get("audit_sample_rate", 0.02)),
            stage2_threshold=int(taxonomy.get("stage2_interest_threshold", 6)),
            realtime=realtime,
        )
    except BudgetExceeded as exc:
        typer.secho(f"audit stopped at the budget cap: {exc}", fg=typer.colors.YELLOW, err=True)
        _print_spend(store, settings)
        raise typer.Exit(3) from exc
    typer.echo(f"audit {day}: {counts}")
    _print_spend(store, settings)


@app.command("eval")
def eval_cmd() -> None:
    """Precision/recall of triage against the labels table."""
    setup_logging()
    settings = _settings(need_llm=False)
    from .db import Store
    from .evaluate import evaluate
    from .triage.prompts import load_taxonomy

    taxonomy = load_taxonomy(settings.taxonomy_yaml)
    store = Store(settings.db_path)
    report = evaluate(store, int(taxonomy.get("stage2_interest_threshold", 6)))
    typer.echo(report.render())


@app.command()
def rate(
    situation_id: str = typer.Argument(...),
    score: int = typer.Option(..., "--score", min=1, max=5),
    note: str = typer.Option("", "--note"),
) -> None:
    """Store a 1-5 rating on a surfaced situation (used for future few-shot)."""
    setup_logging()
    settings = _settings(need_llm=False)
    from sqlalchemy import select

    from .db import Store
    from .db.schema import RatingRow, SituationRow

    store = Store(settings.db_path)
    with store.session() as s:
        sit = s.execute(
            select(SituationRow).where(SituationRow.situation_id == situation_id)
        ).scalar_one_or_none()
        if sit is None:
            typer.secho(f"Unknown situation {situation_id}", fg="red", err=True)
            raise typer.Exit(1)
        s.add(RatingRow(situation_id=situation_id, score=score, note=note))
    typer.echo(f"Recorded {score}/5 for {situation_id}")


@company_app.command("show")
def company_show(ticker: str = typer.Argument(..., help="Ticker or market/ticker")) -> None:
    """Print an issuer's company file."""
    setup_logging()
    settings = _settings(need_llm=False)
    from .company import CompanyFiles

    companies = CompanyFiles(settings.companies_dir)
    path = _find_company(companies, ticker)
    if path is None:
        typer.secho(f"No company file for {ticker}", fg="red", err=True)
        raise typer.Exit(1)
    typer.echo(path.read_text(encoding="utf-8"))


@company_app.command("diff")
def company_diff(
    ticker: str = typer.Argument(...),
    since: str = typer.Option(..., "--since", help="YYYY-MM-DD"),
) -> None:
    """Show what changed in an issuer's file since a date."""
    setup_logging()
    settings = _settings(need_llm=False)
    from .company import CompanyFiles

    companies = CompanyFiles(settings.companies_dir)
    path = _find_company(companies, ticker)
    if path is None:
        typer.secho(f"No company file for {ticker}", fg="red", err=True)
        raise typer.Exit(1)
    issuer_key = f"{path.parent.name}/{path.stem}"
    typer.echo(companies.diff_since(issuer_key, since))


def _find_company(companies: CompanyFiles, ticker: str) -> Path | None:
    from .company import CompanyFiles

    assert isinstance(companies, CompanyFiles)
    if "/" in ticker:
        p = companies.paths(ticker).md
        return p if p.exists() else None
    matches = sorted(companies.root.glob(f"*/{ticker.upper()}.md")) + sorted(
        companies.root.glob(f"*/{ticker}.md")
    )
    return matches[0] if matches else None


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
