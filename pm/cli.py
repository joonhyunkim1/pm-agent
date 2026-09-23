"""pm 명령줄 도구."""
from __future__ import annotations

import webbrowser
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from . import discovery, notify, paths, scanner, schedule, secrets
from . import manifest as mf
from .config import load_config
from .notify import telegram
from .store import Store

app = typer.Typer(help="pm-agent — 내 프로젝트를 지켜보는 AI 프로젝트 매니저", no_args_is_help=True)
secret_app = typer.Typer(help="키체인 비밀값 관리", no_args_is_help=True)
tg_app = typer.Typer(help="Telegram 알림 설정", no_args_is_help=True)
sched_app = typer.Typer(help="자동 실행(OS 스케줄러) 관리", no_args_is_help=True)
idea_app = typer.Typer(help="구상 중인 아이디어 메모", no_args_is_help=True)
app.add_typer(secret_app, name="secret")
app.add_typer(tg_app, name="telegram")
app.add_typer(sched_app, name="schedule")
app.add_typer(idea_app, name="idea")

con = Console()
HEALTH = {"red": "[red]●[/]", "yellow": "[yellow]●[/]", "green": "[green]●[/]", "gray": "[dim]●[/]"}


@app.command()
def init(
    path: Optional[str] = typer.Argument(None, help="프로젝트 폴더. 생략하면 --all 필요"),
    all_: bool = typer.Option(False, "--all", help="roots 아래의 등록 안 된 폴더를 모두 등록"),
    force: bool = typer.Option(False, "--force", help="이미 있는 매니페스트도 덮어쓰기"),
):
    """폴더를 보고 매니페스트 초안을 만든다."""
    cfg = load_config()
    existing, _ = mf.load_all()
    if all_:
        targets = [paths.resolve(p) for p in discovery.unregistered(cfg, existing)] if not force \
            else discovery.candidate_dirs(cfg)
    elif path:
        targets = [paths.resolve(path)]
    else:
        con.print("[red]폴더 경로를 주거나 --all을 쓰세요.[/]")
        raise typer.Exit(1)

    for t in targets:
        m = discovery.discover(t, cfg)
        f = mf.manifest_file(m.id)
        if f.exists() and not force:
            con.print(f"[dim]건너뜀[/] {m.name} (이미 있음: {f})")
            continue
        mf.save(m)
        con.print(f"[green]생성[/] {m.name} → {f}")
        con.print(f"   stage={m.stage} stack={','.join(m.stack) or '-'} verify={','.join(m.verify) or '-'} "
                  f"health={len(m.health)} autonomy={m.autonomy}→{m.effective_autonomy}")
    con.print(f"\n매니페스트 폴더: {paths.projects_dir()}")


@app.command("list")
def list_():
    """등록된 프로젝트 목록."""
    manifests, errors = mf.load_all()
    t = Table("id", "이름", "단계", "스택", "자율", "검증", "경로")
    for m in manifests:
        t.add_row(m.id, m.name, m.stage, ",".join(m.stack), f"{m.autonomy}→{m.effective_autonomy}",
                  m.verification_level, m.path)
    con.print(t)
    for e in errors:
        con.print(f"[red]매니페스트 오류[/] {e}")


@app.command()
def scan(
    project: list[str] = typer.Option(None, "--project", "-p", help="이 프로젝트만 (여러 번 가능)"),
    no_notify: bool = typer.Option(False, "--no-notify", help="Telegram 알림 보내지 않기"),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
):
    """지금 스캔한다 (LLM 호출 없음)."""
    try:
        r = scanner.scan("manual", only=project or None, notify_=not no_notify,
                         log=(lambda s: None) if quiet else (lambda s: con.print(s, markup=False, highlight=False)))
    except scanner.ScanBusy as e:
        con.print(f"[yellow]{e}[/]")
        raise typer.Exit(1)
    con.print(f"\n스캔 #{r['run_id']} 완료 ({r['duration_ms'] / 1000:.1f}s), 알림 {r['alerts_sent']}건")
    status()


@app.command()
def status():
    """프로젝트별 건강 상태와 열린 문제."""
    manifests, _ = mf.load_all()
    with Store() as st:
        last = st.last_finished_run()
        t = Table("", "프로젝트", "단계", "위험", "주의", "가장 중요한 문제")
        for m in manifests:
            active = st.findings(m.id, ("open", "acknowledged"))
            has_results = bool(st.latest_results(m.id))
            health = notify.project_health(active) if has_results else "gray"
            crit = sum(1 for f in active if f["severity"] == "critical")
            warn = sum(1 for f in active if f["severity"] == "warning")
            top = next((f["title"] for f in active if f["severity"] != "info"), "")
            t.add_row(HEALTH[health], m.name, m.stage, str(crit or ""), str(warn or ""), top)
    con.print(t)
    if last:
        con.print(f"[dim]마지막 스캔: {last['finished_at']} (#{last['id']}, {last['trigger']})[/]")


@app.command()
def serve(
    port: Optional[int] = typer.Option(None, help="기본값은 config의 dashboard_port"),
    open_: bool = typer.Option(False, "--open", help="브라우저로 열기"),
):
    """대시보드를 연다 (127.0.0.1)."""
    import uvicorn

    port = port or load_config().dashboard_port
    url = f"http://localhost:{port}"
    con.print(f"대시보드: {url}")
    if open_:
        webbrowser.open(url)
    uvicorn.run("pm.api:app", host="127.0.0.1", port=port, log_level="warning")


@app.command()
def tick():
    """(스케줄러용) 스캔 주기가 지났으면 스캔하고, 하루 한 번 요약을 보낸다."""
    scanner.tick(log=lambda s: print(s, flush=True))


@app.command()
def digest(send: bool = typer.Option(False, "--send", help="Telegram으로 보내기")):
    """일일 요약 미리보기."""
    cfg = load_config()
    manifests, _ = mf.load_all()
    with Store() as st:
        if send:
            notify.maybe_digest(st, manifests, cfg, force=True)
            con.print("보냈습니다.")
        else:
            last = st.get_kv("last_digest")
            print(notify.format_digest(st, manifests, last["utc"] if last else None, cfg.dashboard_port))


@app.command()
def config():
    """설정·데이터 폴더 위치."""
    con.print(f"데이터 폴더:   {paths.home()}")
    con.print(f"설정 파일:     {paths.config_file()}")
    con.print(f"매니페스트:    {paths.projects_dir()}")
    con.print(f"DB:            {paths.db_file()}")
    load_config()


# ---- secret ----
@secret_app.command("set")
def secret_set(name: str):
    """비밀값을 키체인에 저장한다 (입력은 화면에 보이지 않음)."""
    value = typer.prompt(f"{name} 값", hide_input=True)
    secrets.put(name, value.strip())
    con.print(f"[green]저장됨[/] {name}")


@secret_app.command("list")
def secret_list():
    """저장된 비밀값 이름 (값은 보여주지 않음)."""
    for n in secrets.names():
        mark = "[green]✓[/]" if secrets.get(n) else "[red]✗ 키체인에 없음[/]"
        con.print(f"{mark} {n}")


@secret_app.command("delete")
def secret_delete(name: str):
    secrets.remove(name)
    con.print(f"삭제됨 {name}")


# ---- telegram ----
@tg_app.command("link")
def tg_link():
    """봇에게 보낸 최근 메시지로 chat id를 찾아 저장한다."""
    if not secrets.get(telegram.TOKEN):
        con.print("[red]먼저 `pm secret set TELEGRAM_BOT_TOKEN`으로 봇 토큰을 저장하세요.[/]")
        raise typer.Exit(1)
    bot = telegram.bot_name()
    found = telegram.latest_chat()
    if not found:
        con.print(f"텔레그램에서 @{bot} 에게 아무 메시지나 보낸 뒤 다시 실행하세요.")
        raise typer.Exit(1)
    chat_id, name = found
    secrets.put(telegram.CHAT, chat_id)
    telegram.send(f"✅ pm-agent 알림이 이 대화({notify.esc(name)})로 연결됐습니다.")
    con.print(f"[green]연결됨[/] @{bot} → {name} ({chat_id})")


@tg_app.command("test")
def tg_test():
    telegram.send("🔔 pm-agent 테스트 메시지")
    con.print("보냈습니다.")


# ---- schedule ----
@sched_app.command("install")
def sched_install(dry_run: bool = typer.Option(False, "--dry-run", help="등록하지 않고 내용만 출력")):
    """1시간마다 `pm tick`이 돌도록 OS 스케줄러에 등록한다."""
    con.print(schedule.install(dry_run=dry_run))


@sched_app.command("uninstall")
def sched_uninstall():
    con.print(schedule.uninstall())


@sched_app.command("status")
def sched_status():
    con.print(schedule.status())


# ---- idea ----
@idea_app.command("add")
def idea_add(title: str, note: str = typer.Option("", "--note", "-n")):
    with Store() as st:
        st.add_idea(title, note)
    con.print(f"[green]추가됨[/] {title}")


@idea_app.command("list")
def idea_list():
    with Store() as st:
        for i in st.ideas():
            con.print(f"#{i['id']} {i['title']}" + (f" — {i['note']}" if i["note"] else ""))


if __name__ == "__main__":
    app()
