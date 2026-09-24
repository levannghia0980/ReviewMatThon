import React, { useState, useEffect, useRef } from 'react';
import { Play, Clipboard, Sparkles, Loader2, Activity } from 'lucide-react';

export default function IngestTab({ onIngestSuccess }) {
  const [url, setUrl] = useState('');
  const [quality, setQuality] = useState('480p');
  const [sourceLang, setSourceLang] = useState('zh');
  const [cleanText, setCleanText] = useState(true);

  const [isLoading, setIsLoading] = useState(false);
  const [currentStep, setCurrentStep] = useState(0); // 0: idle, 1: dl, 2: audio, 3: whisper, 4: done
  const [taskId, setTaskId] = useState(null);
  const [logs, setLogs] = useState([
    { text: '[System] Sẵn sàng nhận link video. Nhập link và nhấn "Bắt Đầu Xử Lý 1-Shot".', type: 'cyan' }
  ]);

  const terminalEndRef = useRef(null);

  // Auto-scroll terminal to bottom on new log
  useEffect(() => {
    if (terminalEndRef.current) {
      terminalEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs]);

  const addLocalLog = (text, type = 'cyan') => {
    const time = new Date().toLocaleTimeString();
    setLogs((prev) => [...prev, { text: `[${time}] ${text}`, type }]);
  };

  const handlePaste = async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text) setUrl(text.trim());
    } catch (e) {
      addLocalLog('Không thể đọc clipboard tự động. Vui lòng nhấn Ctrl+V.', 'amber');
    }
  };

  // Poll background task status
  useEffect(() => {
    if (!taskId || !isLoading) return;

    let isPolling = true;
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`/api/v1/pipeline/status/${taskId}`);
        if (!res.ok) return;

        const data = await res.json();
        if (!isPolling) return;

        // Sync Step
        if (data.step) {
          setCurrentStep(data.step);
        }

        // Sync Logs from server
        if (data.logs && data.logs.length > 0) {
          const serverLogs = data.logs.map((l) => ({
            text: `[${l.time}] ${l.text}`,
            type: l.type || 'cyan'
          }));
          setLogs(serverLogs);
        }

        // Check if finished
        if (data.status === 'completed') {
          setIsLoading(false);
          setTaskId(null);
          setCurrentStep(4);
          clearInterval(interval);
          setUrl('');
          if (onIngestSuccess) onIngestSuccess();
        } else if (data.status === 'failed') {
          setIsLoading(false);
          setTaskId(null);
          setCurrentStep(0);
          clearInterval(interval);
        }
      } catch (err) {
        // Network blip while polling, don't crash
        console.warn('Poll error:', err);
      }
    }, 1200);

    return () => {
      isPolling = false;
      clearInterval(interval);
    };
  }, [taskId, isLoading, onIngestSuccess]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!url.trim() || isLoading) return;

    setIsLoading(true);
    setCurrentStep(1);
    addLocalLog(`[1/4] 🚀 Đang gửi yêu cầu xử lý 1-Shot lên server...`, 'cyan');

    try {
      const res = await fetch('/api/v1/pipeline/ingest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url: url.trim(),
          quality: quality,
          source_language: sourceLang,
          clean_text: cleanText
        })
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Lỗi khởi động pipeline');
      }

      const data = await res.json();
      setTaskId(data.task_id);
      addLocalLog(`⚡ Tác vụ #${data.task_id} đã khởi chạy trong background thread.`, 'cyan');

    } catch (err) {
      setCurrentStep(0);
      setIsLoading(false);
      addLocalLog(`❌ LỖI KHỞI ĐỘNG: ${err.message}`, 'rose');
    }
  };

  return (
    <div className="grid-2">
      {/* Form Card */}
      <div className="glass-panel">
        <div className="panel-header">
          <h3>
            <Sparkles size={18} color="var(--cyan)" />
            Khởi Chạy 1-Shot Pipeline
          </h3>
          <span className="tag-badge cyan">Tự Động Hóa Background</span>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="form-field">
            <label>Link Video (Bilibili / YouTube / Douyin):</label>
            <div className="input-group">
              <input
                type="text"
                placeholder="https://www.bilibili.com/video/BV..."
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                required
              />
              <button type="button" className="btn btn-secondary" onClick={handlePaste}>
                <Clipboard size={14} /> Dán
              </button>
            </div>
          </div>

          <div style={{ display: 'flex', gap: '14px', marginBottom: '18px' }}>
            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', fontSize: '13px', fontWeight: 700, color: 'var(--text-sub)', marginBottom: '8px' }}>
                Chất Lượng Video:
              </label>
              <select value={quality} onChange={(e) => setQuality(e.target.value)}>
                <option value="1080p">🌟 1080p Full HD (Siêu Nét)</option>
                <option value="720p">⚡ 720p HD (Mượt & Nét)</option>
                <option value="480p">🌾 480p SD (Tiêu Chuẩn)</option>
                <option value="360p">💾 360p (Tiết Kiệm Dung Lượng)</option>
                <option value="audio">🎵 Chỉ Tải Audio (16kHz)</option>
              </select>
            </div>

            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', fontSize: '13px', fontWeight: 700, color: 'var(--text-sub)', marginBottom: '8px' }}>
                Ngôn Ngữ Video Gốc:
              </label>
              <select value={sourceLang} onChange={(e) => setSourceLang(e.target.value)}>
                <option value="zh">Tiếng Trung (Bilibili / Anime CN)</option>
                <option value="ja">Tiếng Nhật (Anime JP)</option>
                <option value="vi">Tiếng Việt</option>
                <option value="en">Tiếng Anh</option>
                <option value="auto">Tự Động Nhận Diện</option>
              </select>
            </div>
          </div>

          <div className="form-field">
            <label className="checkbox-card">
              <input
                type="checkbox"
                checked={cleanText}
                onChange={(e) => setCleanText(e.target.checked)}
              />
              <span>Tự động lọc từ đệm tiếng Trung (呃, 啊, 嗯...) & chuẩn hóa text</span>
            </label>
          </div>

          <button
            type="submit"
            className="btn btn-cyan btn-full"
            disabled={isLoading}
            style={{ padding: '14px' }}
          >
            {isLoading ? (
              <>
                <Loader2 className="animate-spin" size={18} />
                <span>Đang xử lý nền (Background Thread)...</span>
              </>
            ) : (
              <>
                <Play size={18} fill="#050b14" />
                <span>BẮT ĐẦU XỬ LÝ 1-SHOT</span>
              </>
            )}
          </button>
        </form>
      </div>

      {/* Live Stepper & Terminal Log */}
      <div className="glass-panel">
        <div className="panel-header">
          <h3>
            <Activity size={18} color="var(--purple)" />
            Tiến Trình & Terminal
          </h3>
          <span className={`tag-badge ${isLoading ? 'violet' : currentStep === 4 ? 'emerald' : 'cyan'}`}>
            {isLoading ? 'Đang Chạy' : currentStep === 4 ? 'Hoàn Tất' : 'Sẵn Sàng'}
          </span>
        </div>

        {/* Stepper */}
        <div className="stepper-container">
          <div className={`step-box ${currentStep >= 1 ? (currentStep > 1 ? 'done' : 'active') : ''}`}>
            <div className="step-num">1</div>
            <div className="step-label">
              <strong>Tải Video</strong>
              <small>input/videos/</small>
            </div>
          </div>
          <div className="step-divider"></div>

          <div className={`step-box ${currentStep >= 2 ? (currentStep > 2 ? 'done' : 'active') : ''}`}>
            <div className="step-num">2</div>
            <div className="step-label">
              <strong>Tách 16k WAV</strong>
              <small>input/audio_raw/</small>
            </div>
          </div>
          <div className="step-divider"></div>

          <div className={`step-box ${currentStep >= 3 ? (currentStep > 3 ? 'done' : 'active') : ''}`}>
            <div className="step-num">3</div>
            <div className="step-label">
              <strong>CapCut STT</strong>
              <small>Bóc timecode</small>
            </div>
          </div>
          <div className="step-divider"></div>

          <div className={`step-box ${currentStep === 4 ? 'done' : ''}`}>
            <div className="step-num">4</div>
            <div className="step-label">
              <strong>Lưu SQLite</strong>
              <small>output/transcripts/</small>
            </div>
          </div>
        </div>

        {/* Terminal Box */}
        <div className="terminal-box">
          {logs.map((log, idx) => (
            <div key={idx} className={`terminal-row ${log.type}`}>
              {log.text}
            </div>
          ))}
          <div ref={terminalEndRef} />
        </div>
      </div>
    </div>
  );
}

