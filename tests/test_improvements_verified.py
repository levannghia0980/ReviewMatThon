import asyncio
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import cv2
import numpy as np
import pytest

from app.api.endpoints.settings import SettingsUpdateRequest, get_settings, update_settings
from app.config import Settings, settings
from app.core import llm_client
from app.schemas.transcript import DialogueSegment
from app.services.step_02_stt.capcut_asr import CapCutASRService
from app.services.step_02_stt.text_cleaner import TextCleanerService
from app.services.step_02_stt.visual_sub_refiner import (
    VisualSubRefiner,
    _compute_subtitle_band,
    _crop_from_mask,
    _probe_subtitle_roi,
    normalize_cjk,
)
import app.services.step_02_stt.visual_sub_refiner as vsr_mod
from app.services.step_03_translation.dichhan import llm_extractor
from app.services.step_03_translation.llm_translator import extract_batch_entities_pass1_llm
from app.services.step_03_translation.profiles import (
    SCI_FI_APOCALYPSE_PROFILE,
    SYSTEM_REINCARNATION_PROFILE,
    URBAN_PROFILE,
    WUXIA_PROFILE,
)
from app.services.step_03_translation.script_parser import clean_system_tags_from_text
from app.services.step_03_translation.translation_pipeline import is_true_mega_summary

ROOT_DIR = Path(__file__).resolve().parent.parent
PROJECT4_VIDEO = ROOT_DIR / "output" / "projects" / "project_4" / "BV1mtoCBGEdG_p1_raw.mp4"
PROJECT10_VIDEO = ROOT_DIR / "output" / "projects" / "project_10" / "BV1eexY6JECL_raw.mp4"
PROJECT12_VIDEO = ROOT_DIR / "output" / "projects" / "project_12" / "_c9BCLYW3ZA_raw.mp4"
PROJECT13_VIDEO = ROOT_DIR / "output" / "projects" / "project_13" / "c0W_Nu5qIx0_raw.mp4"
PROJECT13_ASR_JSON = ROOT_DIR / "output" / "projects" / "project_13" / "c0W_Nu5qIx0_raw_clean.json"
PROJECT13_RAW_CAPCUT = ROOT_DIR / "output" / "transcripts" / "c0W_Nu5qIx0_raw_clean_raw_capcut.json"


def _old_split_utterance(u: Dict[str, Any], time_offset: float) -> List[DialogueSegment]:
    """Tái hiện đúng ngưỡng chặt câu cũ (gap >= 350ms hoặc len >= 14 & gap >= 220ms)."""
    words = u.get("words", [])
    out: List[DialogueSegment] = []
    if words and len(words) > 1:
        grp: List[Dict[str, Any]] = []
        for w in words:
            if not grp:
                grp.append(w)
                continue
            gap_ms = w.get("start_time", 0) - grp[-1].get("end_time", 0)
            if (gap_ms >= 350) or (len(grp) >= 14 and gap_ms >= 220):
                seg = CapCutASRService._build_word_segment(u, grp, True, time_offset, len(out) + 1)
                if seg:
                    out.append(seg)
                grp = [w]
            else:
                grp.append(w)
        if grp:
            seg = CapCutASRService._build_word_segment(u, grp, True, time_offset, len(out) + 1)
            if seg:
                out.append(seg)
        return out
    return CapCutASRService._split_utterance_to_segments(u, True, time_offset, 1)


def _load_p13_600s_segments() -> List[DialogueSegment]:
    raw_list = json.loads(PROJECT13_ASR_JSON.read_text(encoding="utf-8"))
    segs = [DialogueSegment(**x) for x in raw_list if float(x["start"]) < 600.0]
    for i, s in enumerate(segs, 1):
        s.id = i
    return segs


def test_01_capcut_antichop_and_words_per_line(monkeypatch, tmp_path):
    """Test 1: Chống chặt vụn câu ở CapCutASRService (words_per_line=28 + ngưỡng tách từ mới)."""
    if not PROJECT13_RAW_CAPCUT.exists():
        pytest.skip("Khong tim thay c0W_Nu5qIx0_raw_clean_raw_capcut.json")

    raw_chunks = json.loads(PROJECT13_RAW_CAPCUT.read_text(encoding="utf-8"))

    # A. So sanh 0..600s tren chunk 0
    chunk0 = raw_chunks[0]
    off0 = float(chunk0.get("offset", 0.0))
    old_600: List[DialogueSegment] = []
    new_600: List[DialogueSegment] = []
    for u in chunk0.get("utterances", []):
        if (u.get("start_time", 0) / 1000.0) + off0 >= 600.0:
            continue
        old_600.extend(_old_split_utterance(u, off0))
        new_600.extend(CapCutASRService._split_utterance_to_segments(u, True, off0, len(new_600) + 1))

    new_600_clean = TextCleanerService.clean_segments(new_600, is_chinese=True)
    new_600_fallback = CapCutASRService._merge_micro_fragments(new_600_clean, is_chinese=True)

    # B. So sanh toan bo 7 chunks sau _merge_overlapping_chunks
    chunks_meta = [{"offset": float(c.get("offset", 0.0))} for c in raw_chunks]
    old_chunk_res = []
    new_chunk_res = []
    for c in raw_chunks:
        off = float(c.get("offset", 0.0))
        o_segs: List[DialogueSegment] = []
        n_segs: List[DialogueSegment] = []
        for u in c.get("utterances", []):
            o_segs.extend(_old_split_utterance(u, off))
            n_segs.extend(CapCutASRService._split_utterance_to_segments(u, True, off, len(n_segs) + 1))
        old_chunk_res.append(o_segs)
        new_chunk_res.append(n_segs)

    old_full = CapCutASRService._merge_overlapping_chunks(old_chunk_res, chunks_meta, overlap_sec=60.0)
    new_full = CapCutASRService._merge_overlapping_chunks(new_chunk_res, chunks_meta, overlap_sec=60.0)
    new_full_fallback = CapCutASRService._merge_micro_fragments(
        TextCleanerService.clean_segments(new_full, is_chinese=True), is_chinese=True
    )

    print(
        f"\n[TEST 1 - CapCut Anti-Chop] "
        f"600s: {len(old_600)} -> {len(new_600)} (fallback micro={len(new_600_fallback)}) | "
        f"Full 2h10m: {len(old_full)} -> {len(new_full)} (fallback micro={len(new_full_fallback)})"
    )

    assert len(new_600) < len(old_600)
    assert len(old_600) - len(new_600) >= 25
    assert len(new_full) < len(old_full)
    assert len(old_full) - len(new_full) >= 180

    # Kiem tra cac cau tung bi chat vuon kinh dien o 0..600s da lien lai trong new_600
    joined_texts = [s.text for s in new_600]
    assert any("上一世抽卡机降临世界" in t for t in joined_texts)
    assert any("阿岩时间还没到" in t for t in joined_texts)
    assert any("谢军你在饮料里放了什么" in t for t in joined_texts)

    # Kiem tra _merge_micro_fragments gom manh vun <= 3 chu sat nhau (gap <= 0.08s) cho fallback audio-only
    micro_demo = [
        DialogueSegment(id=1, start=1.0, end=1.4, duration=0.4, text="这个是", clean_text="这个是"),
        DialogueSegment(id=2, start=1.44, end=2.2, duration=0.76, text="谁家的孩子呀", clean_text="谁家的孩子呀"),
        DialogueSegment(id=3, start=2.8, end=3.4, duration=0.6, text="下一句。", clean_text="下一句。"),
    ]
    merged_micro = CapCutASRService._merge_micro_fragments(micro_demo, is_chinese=True)
    assert len(merged_micro) == 2
    assert merged_micro[0].clean_text == "这个是谁家的孩子呀"

    # C. Xac nhan _transcribe_single_chunk truyen words_per_line=28 toi CapCutClient
    captured_wpl: List[int] = []

    class FakeClient:
        def upload_audio(self, *args, **kwargs):
            return SimpleNamespace(vid="fake_vid", md5="fake_md5", duration_ms=5000)

        def create_stt_task(self, **kwargs):
            captured_wpl.append(kwargs.get("words_per_line"))
            return {"data": {"tasks": [{"id": "fake_task", "token": "fake_tok"}]}}

        def query_stt_task(self, *args, **kwargs):
            return {"data": {"tasks": [{"status": "success", "payload": json.dumps({"utterances": []})}]}}

    dummy_wav = tmp_path / "dummy.wav"
    dummy_wav.write_bytes(b"RIFF")
    monkeypatch.setattr("app.services.step_02_stt.capcut_asr.CapCutClient", FakeClient)
    monkeypatch.setattr(
        "app.services.step_02_stt.capcut_asr.AudioExtractorService.get_audio_duration",
        staticmethod(lambda _: 5.0),
    )
    CapCutASRService._transcribe_single_chunk(
        audio_path=str(dummy_wav), language="zh-CN", use_translation=False, timeout=10.0
    )
    assert captured_wpl == [28]


def test_02_visual_geometry_and_synthetic_crops():
    """Test 2: Tinh toan vung crop cho ca 16:9, 9:16 (Doc), 720p va co lap dong chu."""
    crop_1080, bw_1080, bh_1080 = _compute_subtitle_band(1920, 1080)
    assert (bw_1080, bh_1080) == (1440, 180)
    assert "crop=" in crop_1080 and "scale=1440:180" in crop_1080

    crop_916, bw_916, bh_916 = _compute_subtitle_band(1080, 1920)
    assert (bw_916, bh_916) == (1080, 180)
    m = re.match(r"crop=(\d+):(\d+):(\d+):(\d+)", crop_916)
    assert m is not None
    cw, ch, cx, cy = [int(v) for v in m.groups()]
    assert cw % 2 == 0 and ch % 2 == 0 and cx % 2 == 0 and cy % 2 == 0
    assert cx + cw <= 1080 and cy + ch <= 1920

    crop_720, bw_720, bh_720 = _compute_subtitle_band(1280, 720)
    assert (bw_720, bh_720) == (1440, 180)
    assert "scale=1440:180" in crop_720

    # Kiem tra ROI tu dong co dan cho sub sat mep duoi (360p) va sub nam cao (480p)
    roi_bottom = {"has_hardsub": True, "x0_pct": 0.15, "x1_pct": 0.85, "y0_pct": 0.83, "y1_pct": 1.0, "med_h_pct": 0.08}
    crop_bot, bw_bot, bh_bot = _compute_subtitle_band(640, 360, roi=roi_bottom)
    m_bot = re.match(r"crop=(\d+):(\d+):(\d+):(\d+)", crop_bot)
    assert m_bot is not None
    cw_b, ch_b, cx_b, cy_b = [int(v) for v in m_bot.groups()]
    assert cy_b + ch_b >= 356 and 140 <= bh_bot <= 240 and bw_bot % 2 == 0 and bh_bot % 2 == 0

    roi_high = {"has_hardsub": True, "x0_pct": 0.25, "x1_pct": 0.75, "y0_pct": 0.72, "y1_pct": 0.90, "med_h_pct": 0.05}
    crop_hi, bw_hi, bh_hi = _compute_subtitle_band(852, 480, roi=roi_high)
    m_hi = re.match(r"crop=(\d+):(\d+):(\d+):(\d+)", crop_hi)
    assert m_hi is not None
    _, _, _, cy_hi = [int(v) for v in m_hi.groups()]
    assert cy_hi <= int(480 * 0.74) and 140 <= bh_hi <= 240

    # Tao khung gia lap 16:9 & 9:16 co chu trang o giua va nhieu o goc
    for bw, bh in ((1440, 180), (1080, 180)):
        gray = np.zeros((bh, bw), dtype=np.uint8)
        # Ve "chu" trang giua bang sub + nhieu goc tren trai
        cv2.rectangle(gray, (int(bw * 0.30), int(bh * 0.32)), (int(bw * 0.70), int(bh * 0.72)), 255, -1)
        cv2.rectangle(gray, (5, 5), (25, 25), 255, -1)
        small = cv2.resize(gray, (540, 64), interpolation=cv2.INTER_AREA)
        mask = small > 198
        cropped = _crop_from_mask(mask, gray, bw, bh)
        assert cropped is not None
        assert cropped.ndim == 3 and cropped.shape[2] == 3
        assert cropped.shape[1] < bw * 0.75


def test_03_visual_sub_refiner_live_600s_and_cache(monkeypatch, tmp_path):
    """Test 3 (Live Video 0..600s): Chay that FFmpeg GPU/CPU + PP-OCRv4/v5 + Trong tai + Cache hit + Cleanup."""
    if not (PROJECT13_VIDEO.exists() and PROJECT13_ASR_JSON.exists()):
        pytest.skip("Khong tim thay video/asr project_13 de benchmark truc tiep")

    # Co lap TEMP_DIR de khong lam ban cache full video 2h10m cua nguoi dung
    monkeypatch.setattr(vsr_mod, "TEMP_DIR", tmp_path)

    segs_600 = _load_p13_600s_segments()
    assert len(segs_600) == 321

    subs, ext_stats = VisualSubRefiner.extract_visual_blocks(
        video_path=str(PROJECT13_VIDEO), fps=2.0, max_duration_sec=600.0
    )
    refined, ref_stats = VisualSubRefiner.refine_asr_segments(
        segments=segs_600, subs=subs, video_path=str(PROJECT13_VIDEO), use_cache=True
    )

    print(
        f"\n[TEST 3 - VisualSubRefiner LIVE 0..600s] "
        f"HW={ext_stats.get('hwaccel')} | Frames={ext_stats.get('frames')} | "
        f"Decode={ext_stats.get('t_decode')}s | ExtractWall={ext_stats.get('t_extract_wall')}s | Subs={len(subs)}\n"
        f"  ASR {ref_stats['total_in']} -> {ref_stats['total_out']} cau | "
        f"Exact={ref_stats['exact']} (MultiWit={ref_stats['multi_witness_saved']}) | "
        f"Superset={ref_stats['safe_superset']} | AutoFused={ref_stats['auto_fused']} | "
        f"AliasPolished={ref_stats['alias_polished']} | BleedGuard={ref_stats['bleed_guarded']} | "
        f"Unmatched={ref_stats['unmatched']} | t_MW={ref_stats['t_multi_witness']}s | t_Arb={ref_stats['t_arbiter']}s"
    )

    assert len(subs) >= 280
    assert ext_stats["frames"] >= 1190
    assert ref_stats["total_out"] < ref_stats["total_in"]
    assert ref_stats["exact"] >= 195
    assert ref_stats["bleed_guarded"] >= 1
    assert ref_stats["alias_polished"] >= 1

    # Kiem tra cac loi dong am va chong lap da duoc sua dung
    all_out_norm = [normalize_cjk(s.clean_text) for s in refined]
    assert any("含恨而死" in t for t in all_out_norm)
    assert any("钱呢" in t for t in all_out_norm)
    assert any("阿言你可说话算话" in t for t in all_out_norm)
    assert any("林言你敢这样跟我说话" in t for t in all_out_norm)
    # Khong sinh cac dong trung lap lien tiep nhau sau gop
    assert all(all_out_norm[k] != all_out_norm[k + 1] for k in range(len(all_out_norm) - 1))

    # Kiem tra Cache hit o lan chay thu 2 (< 2.5s va 0 goi ONNX)
    cached_subs = VisualSubRefiner.try_load_cached_subs(str(PROJECT13_VIDEO))
    assert len(cached_subs) == len(subs)

    t_cache0 = time.time()
    refined_cached, cache_stats = VisualSubRefiner.refine_asr_segments(
        segments=segs_600, subs=[], video_path=str(PROJECT13_VIDEO), use_cache=True
    )
    t_cache_wall = round(time.time() - t_cache0, 3)

    print(f"  [Cache 2nd Pass] Wall={t_cache_wall}s | MW={cache_stats['t_multi_witness']}s | TotalOut={cache_stats['total_out']}")
    assert t_cache_wall < 2.5
    assert cache_stats["t_multi_witness"] == 0.0
    assert [s.clean_text for s in refined_cached] == [s.clean_text for s in refined]


def test_04_capcut_transcribe_concurrent_visual_and_fallback(monkeypatch, tmp_path):
    """Test 4: CapCutASRService.transcribe chay song song Visual + Cloud STT, va fallback khi tat Visual."""
    fake_audio = tmp_path / "sample.wav"
    fake_video = tmp_path / "sample.mp4"
    fake_audio.write_bytes(b"RIFF")
    fake_video.write_bytes(b"MP4")

    monkeypatch.setattr(settings, "OUTPUT_TRANSCRIPTS_DIR", tmp_path / "transcripts")
    monkeypatch.setattr(settings, "STT_TIME_OFFSET_MS", 100)
    monkeypatch.setattr(
        "app.services.step_02_stt.capcut_asr.AudioExtractorService.get_audio_duration",
        staticmethod(lambda _: 10.0),
    )
    monkeypatch.setattr(
        "app.services.step_02_stt.capcut_asr.AudioExtractorService.split_audio_chunks_with_overlap",
        staticmethod(lambda p, **kw: [{"path": p, "offset": 0.0}]),
    )

    events: List[str] = []

    def fake_single_chunk(**kwargs):
        events.append("stt_start")
        time.sleep(0.05)
        events.append("stt_end")
        segs = [
            DialogueSegment(id=1, start=1.0, end=1.5, duration=0.5, text="这个是", clean_text="这个是"),
            DialogueSegment(id=2, start=1.54, end=2.2, duration=0.66, text="谁家的孩子呀", clean_text="谁家的孩子呀"),
        ]
        return segs, [{"id": 1, "text": "这个是"}]

    def fake_extract(video_path, **kwargs):
        events.append("vis_start")
        time.sleep(0.05)
        events.append("vis_end")
        return [{"id": 1, "start": 0.9, "end": 2.3, "n": "这个是谁家的孩子呀"}], {"t_extract_wall": 0.05, "hwaccel": "mock"}

    seen_start_before_offset: List[float] = []

    def fake_refine(segments, subs, video_path=None, use_cache=True):
        seen_start_before_offset.append(segments[0].start)
        merged = [
            DialogueSegment(id=1, start=1.0, end=2.2, duration=1.2, text="这个是谁家的孩子呀", clean_text="这个是谁家的孩子呀")
        ]
        return merged, {
            "total_in": len(segments),
            "total_out": 1,
            "exact": 1,
            "multi_witness_saved": 0,
            "safe_superset": 0,
            "auto_fused": 0,
            "alias_polished": 0,
            "bleed_guarded": 0,
            "t_arbiter": 0.01,
        }

    monkeypatch.setattr(CapCutASRService, "_transcribe_single_chunk", staticmethod(fake_single_chunk))
    monkeypatch.setattr(VisualSubRefiner, "try_load_cached_subs", classmethod(lambda cls, vp: []))
    monkeypatch.setattr(VisualSubRefiner, "extract_visual_blocks", staticmethod(fake_extract))
    monkeypatch.setattr(VisualSubRefiner, "refine_asr_segments", classmethod(lambda cls, *a, **kw: fake_refine(*a, **kw)))
    monkeypatch.setattr(settings, "ENABLE_VISUAL_SUB_REFINER", True)

    segs, *_ = CapCutASRService.transcribe(audio_path=str(fake_audio), language="zh-CN", video_path=str(fake_video))
    assert seen_start_before_offset == [1.0]  # Refine truoc khi tru 100ms offset
    assert round(segs[0].start, 3) == 0.9  # Offset 0.1s ap dung sau refine
    assert len(segs) == 1
    assert set(events) == {"stt_start", "stt_end", "vis_start", "vis_end"}
    # Song song: tac vu thu 2 phai bat dau truoc khi ca 2 ket thuc
    assert events.index("vis_start") < max(events.index("stt_end"), events.index("vis_end"))

    # Kiem tra khi tat VisualSubRefiner -> Fallback _merge_micro_fragments hoat dong
    events.clear()
    monkeypatch.setattr(settings, "ENABLE_VISUAL_SUB_REFINER", False)
    segs_fb, *_ = CapCutASRService.transcribe(audio_path=str(fake_audio), language="zh-CN", video_path=str(fake_video))
    assert "vis_start" not in events
    assert len(segs_fb) == 1 and segs_fb[0].clean_text == "这个是谁家的孩子呀"


def test_05_llm_client_max_output_tokens_and_thinking_config(monkeypatch):
    """Test 5: llm_client.py bat maxOutputTokens=65536 va truyen dung thinkingConfig."""
    captured_payloads: List[Dict[str, Any]] = []

    async def fake_no_sleep(*args, **kwargs):
        return None

    async def fake_post(client, url, headers, payload, max_retries=5):
        captured_payloads.append(payload)

        class FakeResp:
            status_code = 200
            text = "{}"

            @staticmethod
            def json():
                return {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]}

        return FakeResp()

    monkeypatch.setattr(llm_client.asyncio, "sleep", fake_no_sleep)
    monkeypatch.setattr(llm_client, "get_active_gemini_keys", lambda: ["fake-key-123"])
    monkeypatch.setattr(llm_client, "post_gemini_with_retry", fake_post)

    async def _run_cases():
        await llm_client.execute_llm_call("p", provider="gemini", model="gemini-2.5-flash", thinking_budget=0)
        await llm_client.execute_llm_call("p", provider="gemini", model="gemini-2.5-flash", thinking_budget=-1)
        await llm_client.execute_llm_call("p", provider="gemini", model="gemini-2.5-flash", thinking_budget=1024)
        await llm_client.execute_llm_call("p", provider="gemini", model="gemini-2.5-pro", thinking_budget=0)
        await llm_client.execute_llm_call("p", provider="gemini", model="gemini-2.0-flash", thinking_budget=1024)

    asyncio.run(_run_cases())

    assert len(captured_payloads) == 5
    for pl in captured_payloads:
        assert pl["generationConfig"]["maxOutputTokens"] == 65536

    assert captured_payloads[0]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
    assert captured_payloads[1]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": -1}
    assert captured_payloads[2]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 1024}
    assert captured_payloads[3]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 128}
    assert "thinkingConfig" not in captured_payloads[4]["generationConfig"]


def test_06_entity_metadata_preservation_and_batch2_prompt(monkeypatch):
    """Test 6: Bao toan gender & role qua Pass 1 va khong mat du lieu o Batch 2+."""

    async def fake_direct_llm(**kwargs):
        return [
            {
                "chinese_name": "林雪",
                "vietnamese_name": "Lâm Tuyết",
                "entity_type": "NAME",
                "gender": "female",
                "role": "nữ chính, cảnh sát hình sự",
            }
        ]

    with monkeypatch.context() as m_p1:
        m_p1.setattr(llm_extractor, "extract_batch_entities_direct_llm", fake_direct_llm)
        p1_ents = asyncio.run(
            extract_batch_entities_pass1_llm(
                batch_raw_text="林雪走了进来。", genre="urban", provider="gemini"
            )
        )
    assert len(p1_ents) == 1
    assert p1_ents[0]["gender"] == "female"
    assert p1_ents[0]["role"] == "nữ chính, cảnh sát hình sự"
    assert "Giới tính: female" in p1_ents[0]["desc"]
    assert "nữ chính" in p1_ents[0]["desc"]

    # Gọi lại extract_batch_entities_direct_llm cho Batch 2+ với định dạng key từ Pass 1 {raw, viet, type, gender, role, desc}
    captured_prompts: List[str] = []

    async def fake_exec(prompt, **kwargs):
        captured_prompts.append(prompt)
        return json.dumps({"entities": []}, ensure_ascii=False)

    monkeypatch.setattr(llm_extractor, "execute_llm_call", fake_exec)
    existing_batch2 = {
        "林雪": p1_ents[0],
        "陈风": {
            "vietnamese_name": "Trần Phong",
            "entity_type": "NAME",
            "gender": "male",
            "role": "đội trưởng đội đặc nhiệm",
        },
    }
    asyncio.run(
        llm_extractor.extract_batch_entities_direct_llm(
            combined_raw_text="林雪和陈风一起出警。",
            existing_entities=existing_batch2,
            genre_key="urban",
            provider="gemini",
        )
    )
    prompt_b2 = captured_prompts[0]
    assert "林雪 -> Lâm Tuyết [NAME]" in prompt_b2
    assert "Giới tính: female" in prompt_b2
    assert "nữ chính, cảnh sát hình sự" in prompt_b2
    assert "陈风 -> Trần Phong [NAME]" in prompt_b2
    assert "Giới tính: male" in prompt_b2
    assert "đội trưởng đội đặc nhiệm" in prompt_b2
    assert "(Chưa rõ)" not in prompt_b2


def test_07_profiles_modern_addressing():
    """Test 7: Loai bo xung ho co trang 'Ta - Nguoi' trong tang doi khang cua the loai hien dai."""
    for name, prof in (
        ("URBAN_PROFILE", URBAN_PROFILE),
        ("SYSTEM_REINCARNATION_PROFILE", SYSTEM_REINCARNATION_PROFILE),
        ("SCI_FI_APOCALYPSE_PROFILE", SCI_FI_APOCALYPSE_PROFILE),
    ):
        desc = prof["description"]
        assert "TUYỆT ĐỐI CẤM dùng 'Ta - Ngươi'" in desc, f"{name} thieu quy tac cam Ta - Nguoi"
        layer3_match = re.search(r"3\.\s*Tầng Đối kháng[^\n]+", desc)
        assert layer3_match is not None
        layer3 = layer3_match.group(0)
        assert "Dùng xưng hô hiện đại" in layer3
        # Khong de 'Ta - Nguoi' o phan duoc phep
        allowed_part = layer3.split("TUYỆT ĐỐI CẤM")[0]
        assert "Ta - Ngươi" not in allowed_part

    # The loai co trang van giu binh thuong
    assert "Ta" in WUXIA_PROFILE["description"]


def test_08_mega_summary_guard_and_stage_note_cleanup():
    """Test 8: Chan loi gom >= 10 cau hoac >= 6 cau tom tat cut + Loai bo ngoac chi dan san khau."""
    orig = [{"index": i, "zh": "现在立刻出发去救他"} for i in range(1, 15)]  # 9 chu Han/cau

    assert is_true_mega_summary([{"src": list(range(1, 11)), "vi": "Đi cứu người ngay bây giờ thôi nào các bạn ơi hãy nhanh lên"}], orig) is True
    # 6 cau = 54 chu Han, 6 * 9 * 0.28 = 15.12 -> 5 tu tieng Viet la tom tat cut -> True
    assert is_true_mega_summary([{"src": [1, 2, 3, 4, 5, 6], "vi": "Mọi người cùng xuất phát"}], orig) is True
    # 6 cau voi ban dich day du (>= 16 tu) -> False (khong bat oan)
    assert is_true_mega_summary(
        [{"src": [1, 2, 3, 4, 5, 6], "vi": "Bây giờ chúng ta hãy lập tức xuất phát ngay đến đó để giải cứu anh ấy kẻo không kịp nữa đâu"}],
        orig,
    ) is False
    # Gop binh thuong 4 cau -> False
    assert is_true_mega_summary([{"src": [1, 2, 3, 4], "vi": "Đi cứu người ngay"}], orig) is False

    # Lam sach chi dan san khau nhung giu so lieu & tu khoa hop le
    assert clean_system_tags_from_text("[KHÓA] Mau mở khóa cửa! (thở dài) (1.2s) [max 6 từ]") == "Mau mở khóa cửa!"
    assert clean_system_tags_from_text("Anh ấy quay lại (nói nhỏ đầy ẩn ý) rồi đi mất.") == "Anh ấy quay lại rồi đi mất."
    assert clean_system_tags_from_text("Tỷ lệ sống sót là (50%) vào năm (2025) tại khu (A).") == "Tỷ lệ sống sót là (50%) vào năm (2025) tại khu (A)."
    assert clean_system_tags_from_text("Đưa chìa khóa phòng (302) cho tôi.") == "Đưa chìa khóa phòng (302) cho tôi."


def test_09_settings_toggle_roundtrip(monkeypatch, tmp_path):
    """Test 9: Bat/tat ENABLE_VISUAL_SUB_REFINER luu & doc lai chuan qua .env & API settings."""
    fake_env = tmp_path / ".env"
    fake_env.write_text("ASR_ENGINE=capcut\nENABLE_VISUAL_SUB_REFINER=True\n", encoding="utf-8")
    orig_val = settings.ENABLE_VISUAL_SUB_REFINER
    monkeypatch.setattr(Settings, "ENV_PATH", fake_env)

    try:
        res_off = asyncio.run(update_settings(SettingsUpdateRequest(enable_visual_sub_refiner=False)))
        assert res_off["settings"]["enable_visual_sub_refiner"] is False
        assert settings.ENABLE_VISUAL_SUB_REFINER is False
        assert get_settings()["enable_visual_sub_refiner"] is False

        Settings.reload_from_env()
        assert settings.ENABLE_VISUAL_SUB_REFINER is False

        res_on = asyncio.run(update_settings(SettingsUpdateRequest(enable_visual_sub_refiner=True)))
        assert res_on["settings"]["enable_visual_sub_refiner"] is True
        Settings.reload_from_env()
        assert settings.ENABLE_VISUAL_SUB_REFINER is True
    finally:
        settings.ENABLE_VISUAL_SUB_REFINER = orig_val
        Settings.ENABLE_VISUAL_SUB_REFINER = orig_val


def test_10_adaptive_multi_layout_and_circuit_breaker():
    """Test 10: Auto-ROI da bo cuc (sat mep duoi, nam cao, khong hardsub) + Cau chi an toan toan phim."""
    # A. Phim 360p sub sat mep duoi (project_4: Y=88.6%..99.7%)
    if PROJECT4_VIDEO.exists():
        subs_p4, st_p4 = VisualSubRefiner.extract_visual_blocks(
            video_path=str(PROJECT4_VIDEO), fps=2.0, max_duration_sec=120.0
        )
        print(f"\n[TEST 10A - project_4] ROI={st_p4.get('roi')} | Subs={len(subs_p4)} | Wall={st_p4.get('t_extract_wall')}s")
        assert st_p4.get("has_hardsub") is True
        assert st_p4["roi"]["y1_pct"] >= 0.96
        assert len(subs_p4) >= 15
        assert sum(1 for s in subs_p4 if len(s["n"]) >= 2) >= 12
    else:
        pytest.skip("Thieu project_4 de test sub sat mep duoi")

    # B. Phim 480p sub nam cao (project_10: Y=77.9%..84.6%)
    if PROJECT10_VIDEO.exists():
        subs_p10, st_p10 = VisualSubRefiner.extract_visual_blocks(
            video_path=str(PROJECT10_VIDEO), fps=2.0, max_duration_sec=120.0
        )
        print(f"[TEST 10B - project_10] ROI={st_p10.get('roi')} | Subs={len(subs_p10)} | Wall={st_p10.get('t_extract_wall')}s")
        assert st_p10.get("has_hardsub") is True
        assert st_p10["roi"]["y0_pct"] <= 0.78
        assert len(subs_p10) >= 10
        assert sum(1 for s in subs_p10 if len(s["n"]) >= 2) >= 8
    else:
        pytest.skip("Thieu project_10 de test sub nam cao")

    # C. Phim khong co hardsub (project_12) -> Phai phat hien & bo qua nhanh
    if PROJECT12_VIDEO.exists():
        subs_p12, st_p12 = VisualSubRefiner.extract_visual_blocks(
            video_path=str(PROJECT12_VIDEO), fps=2.0, max_duration_sec=120.0
        )
        print(f"[TEST 10C - project_12 no-hardsub] HW={st_p12.get('hwaccel')} | ROI={st_p12.get('roi')} | Subs={len(subs_p12)}")
        assert st_p12.get("has_hardsub") is False
        assert st_p12.get("hwaccel") == "skipped_no_hardsub"
        assert subs_p12 == []
    else:
        pytest.skip("Thieu project_12 de test skip no-hardsub")

    # D. Cau chi an toan toan phim (Safety Circuit Breaker >= 25 cau)
    orig_segs = [
        DialogueSegment(
            id=i + 1,
            start=round(i * 2.0, 2),
            end=round(i * 2.0 + 1.6, 2),
            duration=1.6,
            text=f"现在立刻跟我一起离开这个危险的地方第{i + 1}号",
            clean_text=f"现在立刻跟我一起离开这个危险的地方第{i + 1}号",
            translated_text="",
            confidence=0.9,
        )
        for i in range(28)
    ]

    # Case D1: Ti le khop < 25% (OCR lech han khong trung ky tu/pinyin -> BleedGuard chan 100%)
    orig_d1 = [
        DialogueSegment(
            id=i + 1,
            start=round(i * 2.0, 2),
            end=round(i * 2.0 + 1.6, 2),
            duration=1.6,
            text="现在立刻离开危险地带",
            clean_text="现在立刻离开危险地带",
            translated_text="",
            confidence=0.9,
        )
        for i in range(28)
    ]
    bad_mismatch_subs = [
        {"id": i + 1, "start": round(i * 2.0, 2), "end": round(i * 2.0 + 1.6, 2), "n": "甲乙丙丁戊己庚辛壬癸"}
        for i in range(28)
    ]
    out_d1, st_d1 = VisualSubRefiner.refine_asr_segments(segments=orig_d1, subs=bad_mismatch_subs, use_cache=False)
    assert st_d1["circuit_breaker_triggered"] is True
    assert [s.clean_text for s in out_d1] == [s.clean_text for s in orig_d1]

    # Case D2: Hut luong chu > 22% tren toan bo tap >= 25 cau (confirm=100% nhung mat > 22% luong chu)
    out_d2, st_d2 = VisualSubRefiner.refine_asr_segments(
        segments=orig_segs,
        subs=[{"id": i + 1, "start": round(i * 2.0, 2), "end": round(i * 2.0 + 1.6, 2), "n": "风马牛不相及的字"} for i in range(28)],
        use_cache=False,
    )
    assert st_d2["circuit_breaker_triggered"] is True
    assert [s.clean_text for s in out_d2] == [s.clean_text for s in orig_segs]


