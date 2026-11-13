import logging
import sqlite3
from pathlib import Path

import pytest
from pydub import AudioSegment
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 - register all ORM tables on Base.metadata
from app.db.session import Base
from app.models import (
    DialogueSegmentModel,
    LLMMergedSegmentModel,
    ProjectTask,
    SubtitleSegmentModel,
    TTSDialogueSegmentModel,
)
from app.schemas.transcript import DialogueSegment
from app.services.step_02_stt.visual_sub_refiner import VisualSubRefiner
from app.services.step_04_tts.async_tts_engine import split_text_strictly_by_punctuation
from app.services.step_04_tts.capcut_tts_service import CapCutTTSService
from app.services.step_04_tts.tts_swollen_rescuer import TTSSwollenRescuer
from app.services.step_04_tts.waveform_sub_syncer import WaveformSubSyncer
from app.services.step_05_video.karaoke_subtitle_service import KaraokeSubtitleService

ROOT_DIR = Path(__file__).resolve().parent.parent
LIVE_DB_PATH = ROOT_DIR / "database" / "review_mat_thon.db"


def _make_sqlite_session(db_file: Path):
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal, engine


def test_split_text_no_midclause_hard_cut_and_protects_abbreviations():
    # 1. Punctuationless long sentence (>22 words) must NOT be chopped mid-word/mid-phrase when allow_hard_word_cut=False
    long_no_punct = (
        "Chiếc xe nhà di động của cậu đã được trang bị hệ thống lọc nước tự động "
        "và nông trại sinh thái thu nhỏ cực kỳ hiện đại"
    )
    assert len(long_no_punct.split()) >= 23
    chunks_no_cut = split_text_strictly_by_punctuation(
        long_no_punct, max_words=22, max_chars=120, allow_hard_word_cut=False
    )
    assert chunks_no_cut == [long_no_punct]

    # When TikTok explicit safety limit is enabled, hard word cut is allowed
    chunks_tiktok = split_text_strictly_by_punctuation(
        long_no_punct, max_words=22, max_chars=120, allow_hard_word_cut=True
    )
    assert len(chunks_tiktok) >= 2
    assert " ".join(chunks_tiktok) == long_no_punct

    # 2. Abbreviations (v.v., TP.HCM) and decimals (3.5) must NOT break on internal dots
    abbr_text = "Hệ Thị Thần, v.v."
    chunks_abbr = split_text_strictly_by_punctuation(abbr_text, max_words=22, max_chars=120, allow_hard_word_cut=False)
    assert all(c.strip() != "v ." and c.strip() != "v." for c in chunks_abbr)
    assert any("v.v" in c.replace(" ", "") for c in chunks_abbr)

    decimal_city = "Cần khoảng 3.5 tấn lương thực tại TP.HCM. Đừng chậm trễ!"
    chunks_dec = split_text_strictly_by_punctuation(decimal_city, max_words=22, max_chars=120, allow_hard_word_cut=False)
    assert len(chunks_dec) == 2
    assert "3.5" in chunks_dec[0]
    assert "TP.HCM" in chunks_dec[0]
    assert chunks_dec[1].replace(" !", "!") == "Đừng chậm trễ!"

    # 3. Ellipsis stutters ("Trần...") must NOT isolate 1-2 word fragments
    stutter_text = "Trần... Bình mau kiểm tra ngay đi."
    chunks_stutter = split_text_strictly_by_punctuation(stutter_text, max_words=22, max_chars=120, allow_hard_word_cut=False)
    assert len(chunks_stutter) == 1
    assert "Trần" in chunks_stutter[0] and "Bình mau kiểm tra ngay đi" in chunks_stutter[0]


def test_visual_sub_refiner_trim_adjacent_bleed_and_refine_segments():
    # Reproduction of Project 4 rc#21/22 & rc#199/200 tail bleed
    assert VisualSubRefiner._trim_adjacent_bleed(
        final_s="终于一声轻响谷粒应声脱落",
        a_s="终于是声清响",
        prev_a="",
        next_a="谷粒应声脱落",
    ) == "终于一声轻响"

    assert VisualSubRefiner._trim_adjacent_bleed(
        final_s="把这个煮熟然后再喂猪",
        a_s="把这个煮熟",
        prev_a="",
        next_a="然后再喂猪吧",
    ) == "把这个煮熟"

    # Tail bleed even when next_a has a homophone typo ("再" vs "在")
    assert VisualSubRefiner._trim_adjacent_bleed(
        final_s="把这个煮熟然后再喂猪",
        a_s="把这个煮熟",
        prev_a="",
        next_a="然后在喂猪吧",
    ) == "把这个煮熟"

    # Prefix bleed from previous sentence
    assert VisualSubRefiner._trim_adjacent_bleed(
        final_s="进锅里再加点清水",
        a_s="再加点清水",
        prev_a="先倒进锅里",
        next_a="",
    ) == "再加点清水"

    # Protection: legitimate homophone repair in current sentence must NOT be stripped even if next_a starts similarly
    assert VisualSubRefiner._trim_adjacent_bleed(
        final_s="他每天都去喂猪",
        a_s="他每天都去位猪",
        prev_a="",
        next_a="去喂猪然后休息",
    ) == "他每天都去喂猪"

    # Protection: if a_s already ends with suf, do not strip
    assert VisualSubRefiner._trim_adjacent_bleed(
        final_s="然后再喂猪",
        a_s="然后再喂猪",
        prev_a="",
        next_a="然后再喂猪吗",
    ) == "然后再喂猪"

    # End-to-end refine_asr_segments on synthetic timeline with adjacent subtitle bleed (sub 1 bleeds into sub 2)
    segs = [
        DialogueSegment(id=1, start=1.0, end=3.0, duration=2.0, text="终于是声清响", clean_text="终于是声清响"),
        DialogueSegment(id=2, start=3.2, end=5.2, duration=2.0, text="谷粒应声脱落", clean_text="谷粒应声脱落"),
    ]
    subs = [
        {
            "id": 1,
            "start": 1.0,
            "end": 3.05,
            "text": "终于一声轻响谷粒应声脱落",
            "n": "终于一声轻响谷粒应声脱落",
            "wits": ["终于一声轻响谷粒应声脱落"],
        },
        {
            "id": 2,
            "start": 3.15,
            "end": 5.2,
            "text": "谷粒应声脱落",
            "n": "谷粒应声脱落",
            "wits": ["谷粒应声脱落"],
        },
    ]
    refined, _stats = VisualSubRefiner.refine_asr_segments(segs, subs, video_path=None, use_cache=False)
    assert len(refined) == 2
    assert "谷粒应声脱落" not in (refined[0].clean_text or refined[0].text)
    assert (refined[0].clean_text or refined[0].text) == "终于一声轻响"
    assert (refined[1].clean_text or refined[1].text) == "谷粒应声脱落"


def test_karaoke_ass_strips_stray_hanzi_without_dropping_line(tmp_path, caplog):
    ass_path = tmp_path / "out.ass"
    segs = [
        DialogueSegment(
            id=182,
            start=1.0,
            end=4.0,
            duration=3.0,
            text="刚才看到",
            translated_text="Trước khi ta đến, từng thấy mấy vị sư huynh bên cạnh dược圃 .",
        ),
        DialogueSegment(
            id=183,
            start=4.0,
            end=6.0,
            duration=2.0,
            text="你好世界",
            translated_text="你好世界",
        ),
        DialogueSegment(
            id=184,
            start=6.0,
            end=9.0,
            duration=3.0,
            text="正常",
            translated_text="Câu bình thường.",
        ),
    ]

    with caplog.at_level(logging.WARNING):
        KaraokeSubtitleService.create_karaoke_ass_file(segs, str(ass_path))

    content = ass_path.read_text(encoding="utf-8")
    dialogue_lines = [ln for ln in content.splitlines() if ln.startswith("Dialogue:")]
    assert len(dialogue_lines) == 2
    assert "cạnh dược." in dialogue_lines[0]
    assert "圃" not in content
    assert "你好" not in content
    assert "Câu bình thường." in dialogue_lines[1]
    assert "[ASS_SUB]" in caplog.text


def test_ensure_unmerged_tts_script_and_disable_post_tts_merge(tmp_path):
    SessionLocal, engine = _make_sqlite_session(tmp_path / "pipe.db")
    db = SessionLocal()
    try:
        db.add(ProjectTask(id=1, video_id="vid_test", title="Test Project"))
        db.add_all([
            LLMMergedSegmentModel(
                task_id=1, index=1, batch_index=1, source_indices="1",
                start_time=0.0, end_time=3.0, duration=3.0,
                original_text="第一句", translated_text="Câu đầu tiên rất gọn.",
            ),
            LLMMergedSegmentModel(
                task_id=1, index=2, batch_index=1, source_indices="2",
                start_time=3.2, end_time=6.5, duration=3.3,
                original_text="第二句", translated_text="Hệ Thị Thần, v.v.",
            ),
            LLMMergedSegmentModel(
                task_id=1, index=3, batch_index=1, source_indices="3",
                start_time=6.7, end_time=12.0, duration=5.3,
                original_text="第三句",
                translated_text=(
                    "Chiếc xe nhà di động của cậu đã được trang bị hệ thống lọc nước tự động "
                    "và nông trại sinh thái thu nhỏ rất tiện lợi"
                ),
            ),
        ])
        db.add_all([
            DialogueSegmentModel(
                task_id=1, index=1, batch_index=1, tts_index=1,
                start_time=0.0, end_time=3.0, duration=3.0,
                original_text="第一句", translated_text="Câu đầu tiên rất gọn.",
            ),
            DialogueSegmentModel(
                task_id=1, index=2, batch_index=1, tts_index=1,
                start_time=3.2, end_time=6.5, duration=3.3,
                original_text="第二句", translated_text="Hệ Thị Thần, v.v.",
            ),
            DialogueSegmentModel(
                task_id=1, index=3, batch_index=1, tts_index=1,
                start_time=6.7, end_time=12.0, duration=5.3,
                original_text="第三句",
                translated_text=(
                    "Chiếc xe nhà di động của cậu đã được trang bị hệ thống lọc nước tự động "
                    "và nông trại sinh thái thu nhỏ rất tiện lợi"
                ),
            ),
        ])
        # Simulate previously merged single TTS row and stale broken SUB_KARAOKE rows
        db.add(TTSDialogueSegmentModel(
            task_id=1, tts_index=1, batch_index=1, start_time=0.0, end_time=12.0, duration=12.0,
            original_text="merged", translated_text="merged",
        ))
        db.add_all([
            SubtitleSegmentModel(
                task_id=1, sub_index=1, tts_index=1, batch_index=1,
                start_time=0.0, end_time=6.0, duration=6.0, text_vi="Hệ Thị Thần, v .", text_zh="",
            ),
            SubtitleSegmentModel(
                task_id=1, sub_index=2, tts_index=1, batch_index=1,
                start_time=6.0, end_time=12.0, duration=6.0, text_vi="v .", text_zh="",
            ),
        ])
        db.commit()

        restored = CapCutTTSService.ensure_unmerged_tts_script(1, db)
        assert restored is True

        tts_rows = db.query(TTSDialogueSegmentModel).filter_by(task_id=1).order_by(TTSDialogueSegmentModel.index).all()
        assert len(tts_rows) == 3
        assert [t.tts_index for t in tts_rows] == [1, 2, 3]

        sub_rows = db.query(SubtitleSegmentModel).filter_by(task_id=1).order_by(SubtitleSegmentModel.index).all()
        assert all(sr.source_indices for sr in sub_rows)
        assert all(sr.text_vi.strip() != "v ." for sr in sub_rows)
        # Sentence 3 without punctuation must stay un-chopped
        s3_subs = [sr for sr in sub_rows if sr.tts_index == 3]
        assert len(s3_subs) == 1
        assert "tự động" in s3_subs[0].text_vi

        asr_rows = db.query(DialogueSegmentModel).filter_by(task_id=1).order_by(DialogueSegmentModel.index).all()
        assert [a.tts_index for a in asr_rows] == [1, 2, 3]

        # Idempotency: second call does not recreate when already clean and unmerged
        assert CapCutTTSService.ensure_unmerged_tts_script(1, db) is False

        # Merge disabled must preserve all 3 rows separately
        raw_map = {
            1: AudioSegment.silent(duration=1500),
            2: AudioSegment.silent(duration=1600),
            3: AudioSegment.silent(duration=2500),
        }
        merged_list, merged_audio, offsets = CapCutTTSService.merge_continuous_audio_dialogues(
            tts_rows, raw_map, db, project_id=1, task_id="t1", gap_threshold=0.85, enable_merge=False
        )
        assert len(merged_list) == 3
        assert len(merged_audio) == 3
        assert [offsets[idx][0]["old_tts_index"] for idx in (1, 2, 3)] == [1, 2, 3]
    finally:
        db.close()
        engine.dispose()


def test_tts_swollen_rescuer_syncs_sub_karaoke_in_db(tmp_path):
    SessionLocal, engine = _make_sqlite_session(tmp_path / "rescue.db")
    db = SessionLocal()
    try:
        db.add(ProjectTask(id=1, video_id="vid_rescue", title="Rescue"))
        tts7 = TTSDialogueSegmentModel(
            task_id=1, tts_index=7, batch_index=1, start_time=45.49, end_time=48.65, duration=3.16,
            original_text="陈平小心地撬开果蒂", translated_text="Trần Bình cẩn thận cạy cuống, hạt lúa rơi xuống.",
        )
        tts8 = TTSDialogueSegmentModel(
            task_id=1, tts_index=8, batch_index=1, start_time=49.0, end_time=51.0, duration=2.0,
            original_text="下一句", translated_text="Câu tiếp theo.",
        )
        db.add_all([tts7, tts8])
        # Seed 3 stale broken subtitles for tts_index=7 (mimicking P4 TTS#7) plus 1 for tts_index=8
        db.add_all([
            SubtitleSegmentModel(
                task_id=1, sub_index=1, tts_index=7, source_indices="7", batch_index=1,
                start_time=45.49, end_time=46.62, duration=1.13,
                text_vi="Trần Bình cẩn thận cạy phần cuống nhỏ nối hạt lúa với bông lúa ,",
                text_zh="",
            ),
            SubtitleSegmentModel(
                task_id=1, sub_index=2, tts_index=7, source_indices="7", batch_index=1,
                start_time=46.62, end_time=47.67, duration=1.05,
                text_vi="cuối cùng một tiếng động nhẹ vang lên , hạt lúa rơi xuống .",
                text_zh="",
            ),
            SubtitleSegmentModel(
                task_id=1, sub_index=3, tts_index=7, source_indices="7", batch_index=1,
                start_time=47.67, end_time=48.65, duration=0.98,
                text_vi="Cuối cùng một tiếng động nhẹ vang lên, hạt lúa rơi xuống .",
                text_zh="",
            ),
            SubtitleSegmentModel(
                task_id=1, sub_index=4, tts_index=8, source_indices="8", batch_index=1,
                start_time=49.0, end_time=51.0, duration=2.0,
                text_vi="Câu tiếp theo.",
                text_zh="",
            ),
        ])
        db.commit()

        rescued_text = "Trần Bình cẩn thận cạy cuống. Hạt lúa rơi xuống."
        TTSSwollenRescuer._sync_rescued_subtitles_in_db(
            db, 1, {7: {"d_obj": tts7, "clean_text": rescued_text}}
        )
        db.commit()

        subs = db.query(SubtitleSegmentModel).filter_by(task_id=1).order_by(SubtitleSegmentModel.index).all()
        assert [s.sub_index for s in subs] == list(range(1, len(subs) + 1))
        sub7 = [s for s in subs if s.tts_index == 7]
        assert [s.text_vi.replace(" .", ".") for s in sub7] == ["Trần Bình cẩn thận cạy cuống.", "Hạt lúa rơi xuống."]
        assert all(s.source_indices == "7" for s in sub7)
        assert [s.text_vi for s in subs if s.tts_index == 8] == ["Câu tiếp theo."]
    finally:
        db.close()
        engine.dispose()


def test_waveform_sub_syncer_grouped_old_tts_index_prevents_drift(tmp_path):
    SessionLocal, engine = _make_sqlite_session(tmp_path / "sync.db")
    db = SessionLocal()
    try:
        db.add(ProjectTask(id=1, video_id="vid_sync", title="Sync"))
        # Merged chunk tts_index=2 merges old_tts_index=10 (split into 2 sub lines) and old_tts_index=11 (1 sub line)
        db.add_all([
            SubtitleSegmentModel(
                task_id=1, sub_index=1, tts_index=2, source_indices="10", batch_index=1,
                start_time=10.0, end_time=12.0, duration=2.0, text_vi="Câu mười rất dài phần một,", text_zh="",
            ),
            SubtitleSegmentModel(
                task_id=1, sub_index=2, tts_index=2, source_indices="10", batch_index=1,
                start_time=12.0, end_time=14.0, duration=2.0, text_vi="phần hai ngắn.", text_zh="",
            ),
            SubtitleSegmentModel(
                task_id=1, sub_index=3, tts_index=2, source_indices="11", batch_index=1,
                start_time=14.0, end_time=22.0, duration=8.0, text_vi="Câu mười một bắt đầu đúng mốc sóng âm.", text_zh="",
            ),
        ])
        db.commit()

        # chunk_sub_offsets has 2 WAV entries (< 3 sub_models!), previously causing global linear fallback drift!
        chunk_sub_offsets = {
            2: [
                {"old_tts_index": 10, "raw_start_ms": 0, "raw_end_ms": 4000},
                {"old_tts_index": 11, "raw_start_ms": 4150, "raw_end_ms": 12000},
            ]
        }
        WaveformSubSyncer.sync_subtitles_to_waveform(
            db=db,
            project_id=1,
            tts_index=2,
            start_sec=10.0,
            seg_dur_sec=12.0,
            actual_speed=1.0,
            trimmed_dur_sec=12.0,
            trim_meta={"trimmed": False, "cut_head_ms": 0},
            chunk_sub_offsets=chunk_sub_offsets,
            max_allowed_end=22.0,
        )
        db.commit()

        subs = db.query(SubtitleSegmentModel).filter_by(task_id=1, tts_index=2).order_by(SubtitleSegmentModel.index).all()
        assert subs[0].start_time == 10.0
        # Sub 1 must fall strictly within sentence 10's [10.0, 14.0] WAV window
        assert 10.0 < subs[1].start_time < 14.0
        # Sub 2 (sentence 11) must lock to exact 10.0 + 4.150 = 14.15s with ZERO drift!
        assert subs[2].start_time == pytest.approx(14.15, abs=0.01)
        assert subs[2].end_time == 22.0
    finally:
        db.close()
        engine.dispose()


def test_real_db_copy_project3_and_4_unmerge_without_mutating_live_db(tmp_path):
    if not LIVE_DB_PATH.exists():
        pytest.skip("Khong tim thay database/review_mat_thon.db de kiem thu tren ban sao")

    db_copy = tmp_path / "review_mat_thon_copy.db"
    src_con = sqlite3.connect(f"file:{LIVE_DB_PATH}?mode=ro", uri=True)
    dst_con = sqlite3.connect(str(db_copy))
    try:
        src_con.backup(dst_con)
    finally:
        dst_con.close()
        src_con.close()

    engine = create_engine(f"sqlite:///{db_copy}", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    try:
        for pid in (3, 4):
            llm_cnt = db.query(LLMMergedSegmentModel).filter_by(task_id=pid).count()
            if not llm_cnt:
                continue
            prev_tts_cnt = db.query(TTSDialogueSegmentModel).filter_by(task_id=pid).count()
            restored = CapCutTTSService.ensure_unmerged_tts_script(pid, db)
            if prev_tts_cnt != llm_cnt:
                assert restored is True
            assert db.query(TTSDialogueSegmentModel).filter_by(task_id=pid).count() == llm_cnt
            subs = db.query(SubtitleSegmentModel).filter_by(task_id=pid).order_by(SubtitleSegmentModel.index).all()
            if subs:
                assert [s.sub_index for s in subs] == list(range(1, len(subs) + 1))
                assert all(s.source_indices for s in subs)
                assert all(s.text_vi.strip() not in {"v .", "v."} for s in subs)
    finally:
        db.close()
        engine.dispose()
