import React, { useState, useEffect } from 'react';
import { 
  Film, Play, Download, Trash2, RotateCcw, Clock, CheckCircle2, 
  Sparkles, FileText, Search, RefreshCw, Eye
} from 'lucide-react';

export default function VideoGalleryView({ onSelectVideoForStudio, isActive }) {
  const [projects, setProjects] = useState([]);
  const [filter, setFilter] = useState('ALL'); // ALL, RAW, TRANSLATED, COMPLETED
  const [search, setSearch] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    if (isActive !== false) {
      loadProjects();
    }
  }, [isActive]);

  useEffect(() => {
    const handleUpdated = () => {
      loadProjects();
    };
    window.addEventListener('video-projects-updated', handleUpdated);
    return () => window.removeEventListener('video-projects-updated', handleUpdated);
  }, []);

  const loadProjects = async () => {
    setIsLoading(true);
    try {
      const res = await fetch(`/api/v1/projects/?page=1&page_size=100&_t=${Date.now()}`, { cache: 'no-store' });
      if (res.ok) {
        const data = await res.json();
        setProjects(data.items || []);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setIsLoading(false);
    }
  };

  const handleReset = async (id) => {
    if (!window.confirm(`⚠️ BẠN CÓ CHẮC MUỐN RESET VIDEO #${id} VỀ LÚC MỚI TẢI XONG?\n\nThao tác này sẽ:\n✓ Giữ nguyên file video gốc đã tải về\n✗ Xóa toàn bộ câu thoại, bản dịch tiếng Việt\n✗ Xóa toàn bộ audio thuyết minh và video render thành phẩm\n\nVideo sẽ sẵn sàng để xử lý lại từ đầu!`)) return;
    try {
      const res = await fetch(`/api/v1/projects/${id}/reset`, { method: 'POST' });
      if (res.ok) {
        alert(`Đã reset video #${id} về trạng thái ban đầu thành công!`);
        loadProjects();
      } else {
        const err = await res.json();
        alert(`Lỗi: ${err.detail || 'Không thể reset'}`);
      }
    } catch (e) {
      alert(`Lỗi: ${e.message}`);
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm(`Bạn có chắc muốn xóa video ID #${id}?`)) return;
    try {
      const res = await fetch(`/api/v1/projects/${id}`, { method: 'DELETE' });
      if (res.ok) loadProjects();
    } catch (e) {
      alert(`Lỗi: ${e.message}`);
    }
  };

  const filtered = projects.filter(p => {
    const match = p.title.toLowerCase().includes(search.toLowerCase()) || p.video_id.toLowerCase().includes(search.toLowerCase());
    if (!match) return false;

    if (filter === 'RAW') return p.translated_dialogues === 0 && !p.has_final;
    if (filter === 'TRANSLATED') return p.translated_dialogues > 0 && !p.has_final;
    if (filter === 'COMPLETED') return p.has_final || p.status === 'COMPLETED';
    return true;
  });

  const formatSec = (sec) => {
    if (!sec) return '00:00';
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', padding: '16px 20px', maxWidth: '1480px', margin: '0 auto', width: '100%', boxSizing: 'border-box' }}>
      
      {/* Top Header & Filters */}
      <div className="glass-panel" style={{ padding: '16px 20px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '14px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <button
            className={`btn ${filter === 'ALL' ? 'btn-primary' : 'btn-secondary'} btn-sm`}
            onClick={() => setFilter('ALL')}
          >
            🌟 Tất Cả ({projects.length})
          </button>
          <button
            className={`btn ${filter === 'RAW' ? 'btn-primary' : 'btn-secondary'} btn-sm`}
            onClick={() => setFilter('RAW')}
          >
            🟡 Mới Tải Về ({projects.filter(p => p.translated_dialogues === 0 && !p.has_final).length})
          </button>
          <button
            className={`btn ${filter === 'TRANSLATED' ? 'btn-primary' : 'btn-secondary'} btn-sm`}
            onClick={() => setFilter('TRANSLATED')}
          >
            🟣 Đã Dịch Thoại ({projects.filter(p => p.translated_dialogues > 0 && !p.has_final).length})
          </button>
          <button
            className={`btn ${filter === 'COMPLETED' ? 'btn-primary' : 'btn-secondary'} btn-sm`}
            onClick={() => setFilter('COMPLETED')}
          >
            🟢 Đã Hoàn Thành ({projects.filter(p => p.has_final || p.status === 'COMPLETED').length})
          </button>
        </div>

        <div style={{ display: 'flex', gap: '10px', minWidth: '320px' }}>
          <input
            type="text"
            placeholder="🔍 Tìm kiếm video..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ width: '100%', padding: '8px 12px', fontSize: '12px' }}
          />
          <button className="btn btn-secondary btn-sm" onClick={loadProjects} title="Tải lại">
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      {/* Video Cards Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))', gap: '20px' }}>
        {filtered.length === 0 ? (
          <div style={{ gridColumn: '1 / -1', textAlign: 'center', padding: '60px', color: 'var(--text-dim)', background: '#ffffff', borderRadius: '10px', border: '1.5px solid #cbd5e1', boxShadow: '0 4px 12px rgba(15, 23, 42, 0.05)' }}>
            Không tìm thấy video nào theo bộ lọc này.
          </div>
        ) : (
          filtered.map((p) => {
            const isCompleted = p.has_final || p.status === 'COMPLETED';
            const isTranslated = p.translated_dialogues > 0;

            return (
              <div
                key={p.id}
                className="glass-panel"
                style={{
                  padding: '16px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '12px'
                }}
              >
                {/* Video Preview Box */}
                <div style={{
                  position: 'relative',
                  width: '100%',
                  height: '200px',
                  background: '#0f172a',
                  borderRadius: '8px',
                  overflow: 'hidden',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  border: '1px solid var(--border)'
                }}>
                  <video
                    controls
                    preload="none"
                    playsInline
                    style={{ width: '100%', height: '100%', objectFit: 'contain' }}
                    src={`/api/v1/projects/${p.id}/video?type=${isCompleted ? 'final' : 'raw'}`}
                  />
                  <div style={{
                    position: 'absolute',
                    top: '8px',
                    left: '8px',
                    padding: '3px 8px',
                    borderRadius: '4px',
                    fontSize: '10.5px',
                    fontWeight: 800,
                    background: isCompleted ? '#059669' : isTranslated ? '#7c3aed' : '#d97706',
                    color: '#ffffff'
                  }}>
                    {isCompleted ? '✓ ĐÃ HOÀN THÀNH' : isTranslated ? 'ĐÃ DỊCH THOẠI' : 'MỚI TẢI VỀ'}
                  </div>
                </div>

                {/* Info */}
                <div>
                  <h4 style={{ fontSize: '15px', fontWeight: 800, color: 'var(--text-main)', marginBottom: '4px', lineHeight: 1.4 }}>
                    #{p.id} - {p.title}
                  </h4>
                  <div style={{ display: 'flex', gap: '12px', fontSize: '12px', color: 'var(--text-dim)' }}>
                    <span>⏱ {formatSec(p.duration)}</span>
                    <span>💬 {p.total_dialogues} câu</span>
                    <span>📅 {p.created_at?.slice(0, 10) || 'Gần đây'}</span>
                  </div>
                </div>

                {/* Actions */}
                <div style={{ display: 'flex', gap: '8px', marginTop: 'auto', borderTop: '1px solid var(--border)', paddingTop: '12px' }}>
                  {isCompleted ? (
                    <a
                      href={`/api/v1/projects/download-video/${p.id}`}
                      download
                      className="btn btn-emerald btn-sm"
                      style={{ flex: 1, textDecoration: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px', fontSize: '12px' }}
                    >
                      <Download size={13} /> Tải Video MP4
                    </a>
                  ) : (
                    <button
                      className="btn btn-primary btn-sm"
                      onClick={() => onSelectVideoForStudio && onSelectVideoForStudio(p.id)}
                      style={{ flex: 1, fontSize: '12px' }}
                    >
                      ⚡ Chuyển Sang Xưởng Video
                    </button>
                  )}

                  <button
                    className="btn btn-secondary btn-sm"
                    onClick={() => handleReset(p.id)}
                    title="Reset về lúc mới tải xong (giữ lại video gốc, xóa câu thoại/audio/video render)"
                    style={{ padding: '6px 10px', color: 'var(--amber)', borderColor: 'rgba(245, 158, 11, 0.4)' }}
                  >
                    <RotateCcw size={13} />
                  </button>

                  <button
                    className="btn btn-danger btn-sm"
                    onClick={() => handleDelete(p.id)}
                    style={{ padding: '6px 10px' }}
                    title="Xóa vĩnh viễn video"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
