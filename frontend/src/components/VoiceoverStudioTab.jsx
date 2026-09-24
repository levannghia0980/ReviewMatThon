import React, { useState, useEffect } from 'react';
import { 
  Volume2, Play, RefreshCw, Sparkles, CheckCircle2, 
  Download, Sliders, ShieldCheck, Clock, Layers
} from 'lucide-react';
import { ALL_VOICES } from '../constants/voices';

export default function VoiceoverStudioTab({ initialProjectId }) {
  const [projects, setProjects] = useState([]);
  const [selectedProjectId, setSelectedProjectId] = useState(initialProjectId || null);
  const [projectInfo, setProjectInfo] = useState(null);
  const [dialogues, setDialogues] = useState([]);

  // Voice Settings
  const [voiceCode, setVoiceCode] = useState('BV074_streaming');
  const [playbackSpeed, setPlaybackSpeed] = useState(1.0);
  const [autoFitTimeline, setAutoFitTimeline] = useState(true);
  const [applyMastering, setApplyMastering] = useState(true);

  // Status & Progress
  const [isSynthesizing, setIsSynthesizing] = useState(false);
  const [progress, setProgress] = useState(0);
  const [taskId, setTaskId] = useState(null);
  const [audioPreviewMap, setAudioPreviewMap] = useState({});
  const [playingId, setPlayingId] = useState(null);

  useEffect(() => {
    loadProjects();
  }, []);

  useEffect(() => {
    if (initialProjectId) setSelectedProjectId(initialProjectId);
  }, [initialProjectId]);

  useEffect(() => {
    if (selectedProjectId) {
      loadProjectDetails(selectedProjectId);
    }
  }, [selectedProjectId]);

  const loadProjects = async () => {
    try {
      const res = await fetch('/api/v1/projects/?page=1&page_size=50');
      if (res.ok) {
        const data = await res.json();
        setProjects(data.items || []);
        if (!selectedProjectId && data.items?.length > 0) {
          setSelectedProjectId(data.items[0].id);
        }
      }
    } catch (e) {
      console.error(e);
    }
  };

  const loadProjectDetails = async (projId) => {
    try {
      const res = await fetch(`/api/v1/projects/${projId}?page=1&page_size=500`);
      if (res.ok) {
        const data = await res.json();
        setProjectInfo(data.project);
        setDialogues(data.dialogues || []);
      }
    } catch (e) {
      console.error(e);
    }
  };

  // Poll TTS Task
  useEffect(() => {
    let interval = null;
    if (isSynthesizing && taskId) {
      interval = setInterval(async () => {
        try {
          const res = await fetch(`/api/v1/tts/status/${taskId}`);
          if (!res.ok) return;
          const data = await res.json();
          setProgress(data.progress || 0);

          if (data.status === 'completed') {
            setIsSynthesizing(false);
            clearInterval(interval);
            loadProjectDetails(selectedProjectId);
            alert('✔ Đã sản xuất toàn bộ âm thanh lồng tiếng TikTok TTS!');
          } else if (data.status === 'failed') {
            setIsSynthesizing(false);
            clearInterval(interval);
            alert(`Lỗi TTS: ${data.error}`);
          }
        } catch (e) {
          console.error(e);
        }
      }, 1200);
    }
    return () => clearInterval(interval);
  }, [isSynthesizing, taskId]);

  const handleStartBatchTTS = async () => {
    if (!selectedProjectId) return;
    setIsSynthesizing(true);
    setProgress(5);

    try {
      const res = await fetch('/api/v1/tts/project', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: selectedProjectId,
          voice_code: voiceCode,
          apply_mastering: applyMastering,
          playback_speed: playbackSpeed,
          auto_fit_timeline: autoFitTimeline
        })
      });

      if (!res.ok) throw new Error('Không thể khởi chạy TTS');
      const data = await res.json();
      setTaskId(data.task_id);
    } catch (e) {
      setIsSynthesizing(false);
      alert(`Lỗi: ${e.message}`);
    }
  };

  const handlePlaySingleLine = async (d) => {
    const textToSpeak = d.translated_text || d.clean_text || d.original_text;
    if (!textToSpeak) return;

    setPlayingId(d.id);
    try {
      const res = await fetch('/api/v1/tts/single', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: textToSpeak,
          voice_code: voiceCode,
          apply_mastering: applyMastering,
          playback_speed: playbackSpeed
        })
      });

      if (!res.ok) throw new Error('Không thể phát âm thanh');
      const data = await res.json();
      if (data.audio_base64) {
        const audioSrc = `data:audio/mp3;base64,${data.audio_base64}`;
        const audio = new Audio(audioSrc);
        audio.onended = () => setPlayingId(null);
        audio.play();
      }
    } catch (e) {
      setPlayingId(null);
      alert(`Lỗi: ${e.message}`);
    }
  };

  return (
    <div className="tab-container" style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
      
      {/* Top Header & Settings */}
      <div className="glass-panel" style={{ padding: '20px', borderRadius: 'var(--radius-md)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '16px' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '6px' }}>
              <span className="tag-badge emerald" style={{ padding: '4px 10px', fontSize: '11px', fontWeight: 800 }}>
                🎙️ TIKTOK TTS VOICEOVER
              </span>
              <span className="tag-badge cyan" style={{ padding: '4px 10px', fontSize: '11px', fontWeight: 800 }}>
                SMART HEADROOM ALIGNMENT
              </span>
            </div>
            <h2 style={{ fontSize: '20px', fontWeight: 800, color: 'var(--text-main)' }}>
              Sản Xuất Giọng Lồng Tiếng TikTok Chuẩn Khung Giờ
            </h2>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <select
              value={selectedProjectId || ''}
              onChange={(e) => setSelectedProjectId(parseInt(e.target.value, 10))}
              style={{
                padding: '10px 14px',
                background: 'var(--bg-input)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-sm)',
                color: 'var(--text-main)',
                fontSize: '13px',
                fontWeight: 600,
                maxWidth: '320px'
              }}
            >
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  #{p.id} - {p.title}
                </option>
              ))}
            </select>

            <button
              className="btn btn-primary"
              onClick={handleStartBatchTTS}
              disabled={isSynthesizing || !selectedProjectId}
              style={{ background: 'var(--grad-emerald)', color: '#070913', fontWeight: 800 }}
            >
              {isSynthesizing ? (
                <>
                  <RefreshCw size={15} className="animate-spin" /> Đang Tạo TTS ({progress}%)...
                </>
              ) : (
                <>
                  <Sparkles size={15} /> Tạo Audio Toàn Bộ Project
                </>
              )}
            </button>
          </div>
        </div>

        {/* Configuration Bar */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '14px', marginTop: '18px', paddingTop: '16px', borderTop: '1px solid var(--border)' }}>
          <div>
            <label style={{ fontSize: '12px', color: 'var(--text-sub)', fontWeight: 600, display: 'block', marginBottom: '4px' }}>
              Mã Giọng Đọc
            </label>
            <select
              value={voiceCode}
              onChange={(e) => setVoiceCode(e.target.value)}
              style={{ width: '100%', padding: '8px', background: 'var(--bg-input)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', color: 'var(--text-main)' }}
            >
              {ALL_VOICES.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label style={{ fontSize: '12px', color: 'var(--text-sub)', fontWeight: 600, display: 'block', marginBottom: '4px' }}>
              Thuật Toán Khớp Giờ
            </label>
            <div style={{ padding: '8px 10px', background: 'rgba(16,185,129,0.1)', border: '1px solid rgba(16,185,129,0.3)', borderRadius: 'var(--radius-sm)', fontSize: '12px', color: 'var(--emerald)' }}>
              ✔ Smart Headroom (Khớp 100% timecode gốc)
            </div>
          </div>

          <div>
            <label style={{ fontSize: '12px', color: 'var(--text-sub)', fontWeight: 600, display: 'block', marginBottom: '4px' }}>
              Mastering Audio
            </label>
            <div style={{ padding: '8px 10px', background: 'rgba(0,242,254,0.1)', border: '1px solid rgba(0,242,254,0.3)', borderRadius: 'var(--radius-sm)', fontSize: '12px', color: 'var(--cyan)' }}>
              ✔ Clarity Mastering (Lọc ù, làm nét giọng)
            </div>
          </div>
        </div>
      </div>

      {/* Sentence Audition Table */}
      <div className="glass-panel" style={{ padding: '0', borderRadius: 'var(--radius-md)', overflow: 'hidden' }}>
        <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ fontSize: '14px', fontWeight: 700 }}>
            Danh Sách Câu Thoại ({dialogues.length} câu) - Bấm biểu tượng loa để nghe thử từng câu
          </h3>
        </div>

        <div className="table-wrapper" style={{ maxHeight: 'calc(100vh - 380px)', overflowY: 'auto' }}>
          <table className="modern-table">
            <thead>
              <tr>
                <th width="60">#ID</th>
                <th width="130">Mốc Thời Gian</th>
                <th>Bản Dịch Tiếng Việt</th>
                <th width="120" style={{ textAlign: 'center' }}>Nghe Thử</th>
              </tr>
            </thead>
            <tbody>
              {dialogues.map((d) => (
                <tr key={d.id}>
                  <td>
                    <strong style={{ color: 'var(--cyan)', fontFamily: 'var(--font-mono)' }}>
                      #{d.index}
                    </strong>
                  </td>
                  <td>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--amber)' }}>
                      {d.start_time?.toFixed(2)}s ➔ {d.end_time?.toFixed(2)}s
                    </div>
                  </td>
                  <td>
                    <div style={{ fontSize: '13px', fontWeight: 500, color: d.translated_text ? 'var(--text-main)' : 'var(--text-dim)' }}>
                      {d.translated_text || '(Chưa dịch)'}
                    </div>
                  </td>
                  <td style={{ textAlign: 'center' }}>
                    <button
                      className="btn btn-secondary btn-sm"
                      onClick={() => handlePlaySingleLine(d)}
                      disabled={playingId === d.id}
                      style={{ padding: '6px 12px' }}
                    >
                      {playingId === d.id ? <RefreshCw size={12} className="animate-spin" /> : <Play size={12} />} Nghe
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
