from __future__ import annotations

import tempfile
from pathlib import Path

from dataagent.config import Settings
from dataagent.application.agent_runtime import AgentRuntime
from dataagent.application.conversation import ConversationService

REPO = Path(r"d:\newDataAgent")
settings = Settings.load(cwd=REPO)
print("base_url      :", settings.base_url)
print("fast_text_model:", settings.fast_text_model)
print("api_key set   :", bool(settings.api_key))

home = Path(tempfile.mkdtemp(prefix="da_e2e_"))
runtime = AgentRuntime(
    home / "platform",
    include_datajuicer=settings.datajuicer_enabled,
    allow_model_download=settings.allow_model_download,
    datajuicer_python=settings.datajuicer_python,
    datajuicer_process_bin=settings.datajuicer_process_bin,
    datajuicer_timeout_seconds=settings.datajuicer_timeout_seconds,
    allow_datajuicer_candidate_execution=settings.allow_datajuicer_candidate_execution,
    remote_operator_available=bool(settings.api_key),
    vision_model=settings.vision_model,
    vision_api_base_url=settings.vision_api_base_url,
)
assert runtime.conversation_store is not None

service = ConversationService(
    store=runtime.conversation_store,
    agent_runtime=runtime,
    settings=settings,
    gateway=None,  # real ModelGateway
)
print("gateway.configured:", service.gateway.configured)

# Build a real local image directory to point at.
source = home / "images"
source.mkdir()
(source / "a.jpg").write_bytes(b"\xff\xd8\xff\xe0jpg")
(source / "b.jpg").write_bytes(b"\xff\xd8\xff\xe0jpg")

conv = service.create("user_1")
requirement = "去掉不真实、不清晰的图片，把猫和狗分开"

# Case 1: quoted path + Chinese requirement in ONE message (the pain point).
content1 = f'"{source}"{requirement}'
print("\n--- send 1 (quoted path + requirement) ---")
print("content:", content1)
r1 = service.send(thread_id=conv["id"], owner_id="user_1", content=content1)
print("work_order_id:", r1.get("work_order_id"))
print("reply:", (r1.get("reply") or "")[:400])
if r1.get("turn"):
    ts = r1["turn"]["state"].get("task_spec", {})
    print("objective:", ts.get("objective"))
    print("data_source uri:", ts.get("data_sources", [{}])[0].get("uri"))
    print("ambiguities:", len(ts.get("ambiguities") or []))
    print("interrupt kind:", r1["turn"]["interrupts"][0]["value"].get("kind") if r1["turn"].get("interrupts") else None)
print("gateway.calls:", service.gateway.calls if hasattr(service.gateway, "calls") else "n/a")

# Case 2: path adjacent to Chinese (no quotes, path mid-sentence) in a NEW conversation.
conv2 = service.create("user_1")
content2 = f"帮我处理这个目录{source}里的图片，{requirement}"
print("\n--- send 2 (path adjacent to Chinese, new conversation) ---")
print("content:", content2)
r2 = service.send(thread_id=conv2["id"], owner_id="user_1", content=content2)
print("work_order_id:", r2.get("work_order_id"))
print("reply:", (r2.get("reply") or "")[:400])
if r2.get("turn"):
    ts2 = r2["turn"]["state"].get("task_spec", {})
    print("objective:", ts2.get("objective"))
    print("data_source uri:", ts2.get("data_sources", [{}])[0].get("uri"))
print("gateway.calls:", service.gateway.calls if hasattr(service.gateway, "calls") else "n/a")
