import React, { useState, useEffect } from 'react';
import { X, ChevronLeft, ChevronRight, Clock, Globe, Sparkles, FileText, CheckCircle2, AlertCircle, Loader2 } from 'lucide-react';
import { AIREAD_GENRES, DEFAULT_GENRE } from '../constants/genres';

export default function DialogueModal({ isOpen, onClose, projectId, projectTitle, onUpdate }) {
  const [dialogues, setDialogues] = useState([]);
  const [page, setPage] = useState(1);
  const [pageSize] = useState(30);
  const [totalPages, setTotalPages] = useState(1);
  const [totalItems, setTotalItems] = useState(0);
  const [isLoading, setIsLoading] = useState(false);

  // Translation States
  const [isTranslating, setIsTranslating] = useState(false);
  const [genre, setGenre] = useState(DEFAULT_GENRE);
  const [batchSize, setBatchSize] = useState(500);
  const [transTaskId, setTransTaskId] = useState(null);
  const [transLogs, setTransLogs] = useState([]);
  const [transStep, setTransStep] = useState(0);
  const [transProgress, setTransProgress] = useState(0);

  useEffect(() => {
    if (isOpen && projectId) {
      setPage(1);
      loadDialogues(1);
      fetch('/api/v1/settings')
        .then(r => r.json())
        .then(data => {
          if (data.translation_batch_size) setBatchSize(data.translation_batch_size);
        })
        .catch(() => {});
    }
  }, [isOpen, projectId]);

  const loadDialogues = async (pageNum = 1) => {
    setIsLoading(true);
    try {
      const params = new URLSearchParams({ page: pageNum, page_size: pageSize });
      const res = await fetch(`/api/v1/projects/${projectId}/dialogues?${params.toString()}`);
      if (!res.ok) throw new Error('Không thể tải câu thoại');

      const data = await res.json();
      setDialogues(data.items || []);
      setPage(data.page || 1);
      setTotalPages(data.total_pages || 1);
      setTotalItems(data.total_items || 0);
    } catch (err) {
      console.error(err);
    } finally {
      setIsLoading(false);
    }
  };

  // Polling tiến trình dịch
  useEffect(() => {
    if (!transTaskId || !isTranslating) return;
    let active = true;

    const interval = setInterval(async () => {
      try {
        const res = await fetch(`/api/v1/translate/status/${transTaskId}`);
        if (!res.ok) return;
        const data = await res.json();
        if (!active) return;

        if (data.step) setTransStep(data.step);
        if (data.progress) setTransProgress(data.progress);
        if (data.logs && data.logs.length > 0) {
          setTransLogs(data.logs);
        }

        if (data.status === 'completed') {
          setIsTranslating(false);
          setTransTaskId(null);
          loadDialogues(page);
          if (onUpdate) onUpdate();
        } else if (data.status === 'failed') {
          setIsTranslating(false);
          setTransTaskId(null);
        }
      } catch (err) {
        console.warn('Poll translate error:', err);
      }
    }, 1200);

    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [transTaskId, isTranslating, page]);

  const handleStartTranslation = async () => {
    if (isTranslating) return;
    setIsTranslating(true);
    setTransStep(1);
    setTransProgress(5);
    setTransLogs([{ text: '🚀 Đang gửi yêu cầu dịch thuật đa tầng AIREAD lên máy chủ...', type: 'cyan' }]);

    try {
      const res = await fetch('/api/v1/translate/project', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId,
          genre: genre,
          batch_size: batchSize,
          max_chars: 50000,
          provider: 'gemini'
        })
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Lỗi khởi động dịch thuật');
      }

      const data = await res.json();
      setTransTaskId(data.task_id);
    } catch (err) {
      setIsTranslating(false);
      alert(`Lỗi: ${err.message}`);
    }
  };

  if (!isOpen) return null;

  const formatTimecode = (seconds) => {
    if (isNaN(seconds)) return '00:00.000';
    const mins = Math.floor(seconds / 60);
    const secs = (seconds % 60).toFixed(2);
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(5, '0')}`;
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="glass-panel modal-content" style={{ maxWidth: '960px', width: '92%' }} onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="panel-header">
          <div>
            <h3 style={{ fontSize: '18px', color: 'var(--text-main)', display: 'flex', alignItems: 'center', gap: '8px' }}>
              📜 Kịch Bản & Dịch Thuật AIREAD Đa Tầng
            </h3>
            <span style={{ fontSize: '12px', color: 'var(--text-dim)', marginTop: '4px', display: 'block' }}>
              Project #{projectId}: <strong style={{ color: 'var(--cyan)' }}>{projectTitle}</strong> | Tổng: {totalItems} câu thoại
            </span>
          </div>

          <button className="btn btn-secondary btn-sm" onClick={onClose}>
            <X size={16} />
          </button>
        </div>

        {/* Translation Toolbar Control */}
        <div style={{
          background: 'rgba(139, 92, 246, 0.08)',
          border: '1px solid rgba(139, 92, 246, 0.25)',
          borderRadius: '10px',
          padding: '12px 16px',
          marginBottom: '14px',
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '12px'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--purple-light)' }}>Thể Loại:</span>
              <select
                value={genre}
                onChange={(e) => setGenre(e.target.value)}
                disabled={isTranslating}
                style={{ padding: '6px 10px', fontSize: '12px', background: 'rgba(0,0,0,0.4)', borderRadius: '6px' }}
              >
                {AIREAD_GENRES.map((g) => (
                  <option key={g.code} value={g.code}>
                    {g.icon} {g.name}
                  </option>
                ))}
              </select>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--cyan)' }}>Lô Gửi:</span>
              <select
                value={batchSize}
                onChange={(e) => setBatchSize(parseInt(e.target.value, 10))}
                disabled={isTranslating}
                style={{ padding: '6px 10px', fontSize: '12px', background: 'rgba(0,0,0,0.4)', borderRadius: '6px' }}
              >
                <option value={20}>20 câu / lô (Nhanh & Kiểm soát)</option>
                <option value={50}>50 câu / lô (Cân bằng khuyên dùng)</option>
                <option value={100}>100 câu / lô (Lô lớn mượt mạch)</option>
                <option value={200}>200 câu / lô (Siêu tốc tối đa)</option>
              </select>
            </div>
          </div>

          <button
            className="btn btn-cyan"
            onClick={handleStartTranslation}
            disabled={isTranslating || totalItems === 0}
            style={{ padding: '8px 18px', fontSize: '13px', display: 'flex', alignItems: 'center', gap: '8px' }}
          >
            {isTranslating ? (
              <>
                <Loader2 size={16} className="animate-spin" />
                <span>Đang Dịch AI ({transProgress}%)...</span>
              </>
            ) : (
              <>
                <Sparkles size={16} fill="var(--cyan)" />
                <span>Bắt Đầu Dịch AI (AIREAD)</span>
              </>
            )}
          </button>
        </div>

        {/* Translation Live Progress & Logs Box (When active) */}
        {isTranslating && (
          <div style={{
            background: 'rgba(0, 0, 0, 0.4)',
            border: '1px solid rgba(6, 182, 212, 0.3)',
            borderRadius: '8px',
            padding: '10px 14px',
            marginBottom: '14px',
            maxHeight: '120px',
            overflowY: 'auto',
            fontFamily: 'var(--font-mono)',
            fontSize: '11px',
            display: 'flex',
            flexDirection: 'column',
            gap: '4px'
          }}>
            {transLogs.map((log, idx) => (
              <div key={idx} style={{ color: log.type === 'emerald' ? 'var(--emerald)' : log.type === 'rose' ? 'var(--rose)' : log.type === 'purple' ? 'var(--purple-light)' : 'var(--cyan)' }}>
                {log.text}
              </div>
            ))}
          </div>
        )}

        {/* Body Dialogue List (Song Ngữ ZH + VI) */}
        <div style={{ flex: 1, overflowY: 'auto', paddingRight: '6px', maxHeight: '460px' }}>
          {isLoading ? (
            <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-dim)' }}>
              ⏳ Đang nạp {pageSize} câu thoại từ SQLite...
            </div>
          ) : dialogues.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-dim)' }}>
              Không tìm thấy câu thoại nào.
            </div>
          ) : (
            dialogues.map((d) => (
              <div key={d.id} className="dialogue-card-item" style={{ flexDirection: 'row', alignItems: 'flex-start', gap: '14px', padding: '12px 14px' }}>
                <div className="dialogue-index" style={{ marginTop: '2px' }}>#{d.index}</div>
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '6px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <div className="timecode-pill">
                      ⏱ {formatTimecode(d.start_time)} ➔ {formatTimecode(d.end_time)} ({d.duration.toFixed(2)}s)
                    </div>
                    <span className={`tag-badge ${d.translated_text ? 'emerald' : 'cyan'}`} style={{ fontSize: '10px' }}>
                      {d.translated_text ? '✔ Đã Dịch' : 'Chưa Dịch'}
                    </span>
                  </div>

                  {/* Lời thoại gốc tiếng Trung */}
                  <div style={{ fontSize: '13px', color: 'var(--text-dim)', background: 'rgba(255,255,255,0.02)', padding: '6px 10px', borderRadius: '6px' }}>
                    <span style={{ fontSize: '11px', fontWeight: 700, color: 'var(--amber)', marginRight: '6px' }}>ZH:</span>
                    {d.clean_text || d.original_text}
                  </div>

                  {/* Lời thoại dịch tiếng Việt */}
                  {d.translated_text && (
                    <div style={{ fontSize: '14px', color: 'var(--emerald)', fontWeight: 600, background: 'rgba(16, 185, 129, 0.08)', padding: '8px 12px', borderRadius: '6px', border: '1px solid rgba(16, 185, 129, 0.2)' }}>
                      <span style={{ fontSize: '11px', fontWeight: 800, color: 'var(--cyan)', marginRight: '6px' }}>VI:</span>
                      {d.translated_text}
                    </div>
                  )}
                </div>
              </div>
            ))
          )}
        </div>

        {/* Footer Pagination */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', paddingTop: '14px', borderTop: '1px solid var(--border)', marginTop: '12px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <button
              className="btn btn-secondary btn-sm"
              disabled={page <= 1 || isLoading}
              onClick={() => loadDialogues(page - 1)}
            >
              <ChevronLeft size={14} /> Trang Trước
            </button>

            <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', fontWeight: 800, color: 'var(--cyan)' }}>
              Trang {page} / {totalPages}
            </span>

            <button
              className="btn btn-secondary btn-sm"
              disabled={page >= totalPages || isLoading}
              onClick={() => loadDialogues(page + 1)}
            >
              Trang Sau <ChevronRight size={14} />
            </button>
          </div>

          <button className="btn btn-secondary btn-sm" onClick={onClose}>
            Đóng Cửa Sổ
          </button>
        </div>
      </div>
    </div>
  );
}
