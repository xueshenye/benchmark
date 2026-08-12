#!/bin/bash
# Reference solution for the support-bot task: writes the full final package
# implementing all four milestones (KB Q&A + order API + batch/package + i18n/escalation).
set -euo pipefail

mkdir -p /workspace/support_bot /workspace/tests

cat > /workspace/support_bot/__init__.py <<'PYEOF'
"""support_bot — 云购商城(YunGo)客服机器人."""
PYEOF

cat > /workspace/support_bot/kb.py <<'PYEOF'
"""知识库加载与中文检索(朴素子串重叠)。"""
import os
import re

DEFAULT_KB_DIR = "/workspace/knowledge_base"
KB_DIR_ENV = "SUPPORT_KB_DIR"
_KB_FILES = ("products.md", "policies.md", "troubleshooting.md")


def kb_dir() -> str:
    return os.environ.get(KB_DIR_ENV, DEFAULT_KB_DIR)


def load_kb_text(directory: str | None = None) -> str:
    directory = directory or kb_dir()
    parts = []
    for name in _KB_FILES:
        path = os.path.join(directory, name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                parts.append(f.read())
    return "\n\n".join(parts)


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]


def _grams(text: str, n: int) -> set[str]:
    text = re.sub(r"\s+", "", text)
    if len(text) < n:
        return {text} if text else set()
    return {text[i:i + n] for i in range(len(text) - n + 1)}


def retrieve(question: str, kb: str | None = None) -> str | None:
    """返回与问题最相关的知识库段落;完全没有重叠则返回 None。"""
    kb = load_kb_text() if kb is None else kb
    q2 = _grams(question, 2)
    q3 = _grams(question, 3)
    best, best_score = None, 0
    for para in _paragraphs(kb):
        score = sum(1 for g in q2 if g in para) + 2 * sum(1 for g in q3 if g in para)
        if score > best_score:
            best, best_score = para, score
    if best is None or best_score == 0:
        return None
    return re.sub(r"\s+", " ", best).strip()
PYEOF

cat > /workspace/support_bot/orders.py <<'PYEOF'
"""内部订单 API 客户端(仅测试环境)。"""
import json
import os
import re
import urllib.error
import urllib.request

DEFAULT_API_BASE = "http://localhost:8123"
API_BASE_ENV = "SUPPORT_API_BASE"
ORDER_ID_RE = re.compile(r"YGO[\w-]+")

STATUS_ZH = {
    "pending": "待支付",
    "paid": "已支付",
    "shipped": "已发货",
    "delivered": "已送达",
    "cancelled": "已取消",
}
STATUS_EN = {
    "pending": "pending payment",
    "paid": "paid",
    "shipped": "shipped",
    "delivered": "delivered",
    "cancelled": "cancelled",
}


def api_base() -> str:
    return os.environ.get(API_BASE_ENV, DEFAULT_API_BASE).rstrip("/")


def extract_order_id(question: str) -> str | None:
    m = ORDER_ID_RE.search(question)
    return m.group(0) if m else None


def fetch_order(order_id: str) -> dict | None:
    url = f"{api_base()}/api/orders/{order_id}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, json.JSONDecodeError):
        return None


def describe(order: dict, *, en: bool = False) -> str:
    status = order.get("status", "")
    label = STATUS_EN.get(status, status) if en else STATUS_ZH.get(status, status)
    oid = order.get("order_id", "")
    if en:
        return f"Your order {oid} is {label}."
    return f"您的订单 {oid} 当前状态是:{label}。"
PYEOF

cat > /workspace/support_bot/cli.py <<'PYEOF'
"""命令行入口:单条问答 / 批量 / 转人工 + 多语言。"""
import argparse
import datetime
import os
import re
import sys

from .kb import retrieve
from .orders import describe, extract_order_id, fetch_order

_CJK_RE = re.compile(r"[一-鿿]")
ESCALATION_ZH = "抱歉,这个问题我暂时无法回答,已为您转接人工客服,请稍候。"
ESCALATION_EN = "Sorry, I cannot answer that. I have transferred you to a human agent."

EN_FAQ = {
    "watch": "The YunGo Watch S2 is 399 yuan. It has heart-rate monitoring, 50m water "
             "resistance and about 7 days of battery life.",
    "return": "You can return items within 7 days of delivery for a refund. Quality "
              "issues within 15 days can be exchanged free of charge.",
    "payment": "We support Alipay, WeChat Pay, bank cards and Huabei installments.",
    "warranty": "Electronics come with a 1 year warranty for the whole device and 2 "
                "years for main parts.",
    "robovac": "The YunGo RoboVac X1 is 1299 yuan. It features laser navigation, "
               "sweeping and mopping, and auto recharging.",
}
EN_KEYWORDS = [
    (("watch", "smart"), "watch"),
    (("return",), "return"),
    (("pay", "wechat", "alipay"), "payment"),
    (("warranty",), "warranty"),
    (("vacuum", "robot", "robovac"), "robovac"),
]


def _log_escalation(question: str) -> None:
    path = os.path.join(os.getcwd(), "escalations.log")
    line = f"{datetime.datetime.now().isoformat(timespec='seconds')}\t{question}"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def answer_zh(question: str) -> str:
    oid = extract_order_id(question)
    if oid:
        order = fetch_order(oid)
        if order is None:
            return "抱歉,没有查询到该订单,请核对订单号。"
        return describe(order)
    para = retrieve(question)
    if para is not None:
        return para
    _log_escalation(question)
    return ESCALATION_ZH


def answer_en(question: str) -> str:
    oid = extract_order_id(question)
    if oid:
        order = fetch_order(oid)
        if order is None:
            return "Sorry, I could not find that order. Please double-check the order number."
        return describe(order, en=True)
    low = question.lower()
    for keywords, key in EN_KEYWORDS:
        if any(k in low for k in keywords):
            return EN_FAQ[key]
    _log_escalation(question)
    return ESCALATION_EN


def answer(question: str) -> str:
    return answer_zh(question) if _CJK_RE.search(question) else answer_en(question)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="support-bot", description="YunGo 客服机器人")
    parser.add_argument("question", nargs="?", help='顾客问题,如 support-bot "智能手表多少钱"')
    parser.add_argument("--batch", metavar="Q_TXT", help="从文件逐行读取问题")
    parser.add_argument("-o", "--output", metavar="A_TXT", help="把答案逐行写入该文件")
    args = parser.parse_args(argv)

    if args.batch:
        if not args.output:
            print("error: --batch requires -o/--output", file=sys.stderr)
            return 2
        with open(args.batch, encoding="utf-8") as f:
            questions = [ln.strip() for ln in f if ln.strip()]
        answers = [re.sub(r"\s+", " ", answer(q)).strip() for q in questions]
        with open(args.output, "w", encoding="utf-8") as f:
            f.write("\n".join(answers) + "\n")
        return 0

    if not args.question:
        parser.print_help()
        return 0
    print(re.sub(r"\s+", " ", answer(args.question)).strip())
    return 0
PYEOF

cat > /workspace/support_bot/__main__.py <<'PYEOF'
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
PYEOF

cat > /workspace/support-bot <<'SHIMEOF'
#!/usr/bin/env bash
exec python3 -m support_bot "$@"
SHIMEOF
chmod +x /workspace/support-bot

cat > /workspace/tests/test_support_bot.py <<'PYEOF'
"""客服机器人的 pytest 测试(知识库问答 + 批量)。"""
import os
import re
import subprocess
import sys

BASE = "/workspace"


def _run_bot(args: list[str], *, cwd: str = BASE) -> str:
    env = dict(os.environ)
    env["PYTHONPATH"] = BASE
    env["SUPPORT_KB_DIR"] = os.path.join(BASE, "knowledge_base")
    r = subprocess.run(
        [sys.executable, "-m", "support_bot", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_answers_product_price() -> None:
    assert "399" in _run_bot(["智能手表多少钱"])


def test_answers_policy() -> None:
    assert "7天" in _run_bot(["退换货政策是什么"])


def test_batch_writes_one_answer_per_line(tmp_path) -> None:
    q = tmp_path / "q.txt"
    a = tmp_path / "a.txt"
    q.write_text("智能手表多少钱\n退换货政策是什么\n", encoding="utf-8")
    _run_bot(["--batch", str(q), "-o", str(a)], cwd=str(tmp_path))
    lines = [ln for ln in a.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2
    assert "399" in lines[0]
    assert "7天" in lines[1]
PYEOF
