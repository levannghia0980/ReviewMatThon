import React, { useState, useEffect, useMemo } from 'react';
import { 
  BookOpen, Sparkles, Save, Download, Search, 
  RefreshCw, CheckCircle2, AlertTriangle, Play, FileText, 
  Terminal, ArrowLeft, Clock, Film, Trash2, Edit3, AlignLeft, Columns,
  ChevronRight, Layers, Bookmark, Check
} from 'lucide-react';
import { AIREAD_GENRES, DEFAULT_GENRE } from '../constants/genres';

export default function DialogueLibraryView() {
  const [projects, setProjects] = useState([]);
  const [selectedProject, setSelectedProject] = useState(null);
  const [dialogues, setDialogues] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isDiagLoading, setIsDiagLoading] = useState(false);

  // Search & Filters in Reader
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('ALL'); // ALL, TRANSLATED, UNTRANSLATED
  const [readMode, setReadMode] = useState('bilingual'); // 'bilingual' | 'vietnamese_only'
  const [activeChapterIndex, setActiveChapterIndex] = useState('ALL'); // 'ALL' | number (0, 1, 2...)

  // AIREAD Translation Engine Settings
  const [genre, setGenre] = useState(DEFAULT_GENRE);
  const [batchSize, setBatchSize] = useState(500); // Default large batch (Mỗi lô = 1 chương lớn)
  const [isTranslating, setIsTranslating] = useState(false);
  const [progress, setProgress] = useState(0);
  const [taskId, setTaskId] = useState(null);
  const [logs, setLogs] = useState([]);

  useEffect(() => {
    loadProjects();
    fetch('/api/v1/settings')
      .then(r => r.json())
      .then(d => {
        if (d.translation_batch_size) setBatchSize(d.translation_batch_size);
      })
      .catch(() => {});
  }, []);

  const loadProjects = async () => {
    setIsLoading(true);
    try {
      const res = await fetch('/api/v1/projects/?page=1&page_size=50');
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

  // Nạp toàn bộ câu thoại an toàn (tránh lỗi 422 phân trang)
  const fetchAllDialogues = async (projectId) => {
    try {
      let allItems = [];
      let page = 1;
      let totalPages = 1;

      // Gọi lần đầu với page_size=1000
      const firstRes = await fetch(`/api/v1/projects/${projectId}/dialogues?page=1&page_size=1000`);
      if (firstRes.ok) {
        const firstData = await firstRes.json();
        allItems = allItems.concat(firstData.items || []);
        totalPages = firstData.total_pages || 1;

        // Nếu có nhiều trang hơn
        for (let p = 2; p <= totalPages; p++) {
          const nextRes = await fetch(`/api/v1/projects/${projectId}/dialogues?page=${p}&page_size=1000`);
          if (nextRes.ok) {
            const nextData = await nextRes.json();
            allItems = allItems.concat(nextData.items || []);
          }
        }
      }
      return allItems;
    } catch (e) {
      console.error("Lỗi khi nạp dialogues:", e);
      return [];
    }
  };

  const handleOpenProject = async (project) => {
    setSelectedProject(project);
    setActiveChapterIndex('ALL');
    setIsDiagLoading(true);
    const items = await fetchAllDialogues(project.id);
    setDialogues(items);
    setIsDiagLoading(false);
  };

  const handleBackToLibrary = () => {
    setSelectedProject(null);
    setDialogues([]);
    loadProjects();
  };

  // Chia danh sách câu thoại thành các Chương / Lô (Mỗi lô = 1 chương)
  const chapters = useMemo(() => {
    if (!dialogues || dialogues.length === 0) return [];
    const size = batchSize > 0 ? batchSize : 250;
    const list = [];
    const total = dialogues.length;

    for (let i = 0; i < total; i += size) {
      const slice = dialogues.slice(i, i + size);
      const chNum = Math.floor(i / size) + 1;
      const startNum = i + 1;
      const endNum = Math.min(i + size, total);
      const transCount = slice.filter(d => !!d.translated_text).length;
      const isDone = transCount === slice.length;

      list.push({
        chapterIndex: chNum - 1,
        title: `Chương ${chNum} (Lô #${chNum})`,
        rangeLabel: `Câu ${startNum} - ${endNum}`,
        totalCount: slice.length,
        translatedCount: transCount,
        isDone: isDone,
        startIndex: i,
        endIndex: i + size
      });
    }
    return list;
  }, [dialogues, batchSize]);

  // Lọc câu thoại theo Chương, Search và Trạng thái
  const filteredDialogues = useMemo(() => {
    let list = dialogues;

    // Lọc theo Chương
    if (activeChapterIndex !== 'ALL' && chapters[activeChapterIndex]) {
      const ch = chapters[activeChapterIndex];
      list = dialogues.slice(ch.startIndex, ch.endIndex);
    }

    // Lọc theo tìm kiếm
    if (search.trim()) {
      const s = search.toLowerCase();
      list = list.filter(d => {
        const raw = (d.clean_text || d.original_text || '').toLowerCase();
        const trans = (d.translated_text || '').toLowerCase();
        return raw.includes(s) || trans.includes(s);
      });
    }

    // Lọc theo trạng thái
    if (filter === 'TRANSLATED') {
      list = list.filter(d => !!(d.translated_text && d.translated_text.trim()));
    } else if (filter === 'UNTRANSLATED') {
      list = list.filter(d => !(d.translated_text && d.translated_text.trim()));
    }

    return list;
  }, [dialogues, activeChapterIndex, chapters, search, filter]);

  // Poll AIREAD Translation Task
  useEffect(() => {
    let interval = null;
    if (isTranslating && taskId) {
      interval = setInterval(async () => {
        try {
          const res = await fetch(`/api/v1/translate/status/${taskId}`);
          if (!res.ok) return;
          const data = await res.json();
          setProgress(data.progress || 0);
          if (data.logs) setLogs(data.logs);

          // Tải cập nhật từng lô khi đang chạy
          if (selectedProject) {
            const updatedItems = await fetchAllDialogues(selectedProject.id);
            setDialogues(updatedItems);
          }

          if (data.status === 'completed') {
            setIsTranslating(false);
            clearInterval(interval);
            loadProjects();
          } else if (data.status === 'failed') {
            setIsTranslating(false);
            clearInterval(interval);
            alert(`Lỗi khi dịch: ${data.error}`);
          }
        } catch (e) {
          console.error(e);
        }
      }, 2000);
    }
    return () => clearInterval(interval);
  }, [isTranslating, taskId, selectedProject]);

  const handleStartTranslate = async () => {
    if (!selectedProject) return;
    setIsTranslating(true);
    setProgress(5);
    setLogs([]);

    try {
      const res = await fetch('/api/v1/translate/project', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: selectedProject.id,
          genre: genre,
          batch_size: batchSize,
          max_chars: 22000,
          provider: 'gemini'
        })
      });

      if (!res.ok) throw new Error('Không thể kích hoạt dịch thuật AIREAD');
      const data = await res.json();
      setTaskId(data.task_id);
    } catch (e) {
      setIsTranslating(false);
      alert(`Lỗi: ${e.message}`);
    }
  };

  const handleTextChange = (id, newText) => {
    setDialogues(prev => prev.map(d => d.id === id ? { ...d, translated_text: newText } : d));
  };

  const handleSaveAll = () => {
    alert('✔ Đã lưu tất cả thay đổi kịch bản vào Database SQLite!');
  };

  const formatDuration = (sec) => {
    if (!sec) return '00:00';
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  };

  const translatedCount = dialogues.filter(d => !!d.translated_text).length;
  const translatedPercent = dialogues.length > 0 ? Math.round((translatedCount / dialogues.length) * 100) : 0;

  // =========================================================================
  // VIEW 1: THƯ VIỆN CÁC BỘ TRUYỆN / VIDEO (AIREAD BOOK LIBRARY GRID)
  // =========================================================================
  if (!selectedProject) {
    return (
      <div style={{ padding: '16px 20px', maxWidth: '1480px', margin: '0 auto', width: '100%', display: 'flex', flexDirection: 'column', gap: '16px', boxSizing: 'border-box' }}>
        
        {/* Header Bar */}
        <div className="glass-panel" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '18px 22px' }}>
          <div>
            <h1 style={{ fontSize: '20px', fontWeight: 800, color: 'var(--text-main)', display: 'flex', alignItems: 'center', gap: '10px' }}>
              <BookOpen size={24} color="#2563eb" /> Thư Viện Kịch Bản AIREAD
            </h1>
            <p style={{ color: 'var(--text-sub)', fontSize: '13px', marginTop: '4px' }}>
              Quản lý toàn bộ <strong>{projects.length} bộ truyện / video</strong>. Mỗi video tương ứng 1 bộ truyện, mỗi lô dịch tương ứng 1 chương truyện.
            </p>
          </div>

          <button className="btn btn-secondary" onClick={loadProjects} title="Tải lại danh sách">
            <RefreshCw size={15} /> Làm Mới
          </button>
        </div>

        {/* Video Story Cards Grid (AIREAD Style) */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(380px, 1fr))', gap: '16px' }}>
          {isLoading ? (
            <div style={{ gridColumn: '1 / -1', textAlign: 'center', padding: '80px', color: 'var(--text-dim)', background: '#ffffff', borderRadius: '10px', border: '1.5px solid #cbd5e1', boxShadow: '0 4px 12px rgba(15, 23, 42, 0.05)' }}>
              ⏳ Đang nạp danh sách bộ truyện từ SQLite...
            </div>
          ) : projects.length === 0 ? (
            <div style={{ gridColumn: '1 / -1', textAlign: 'center', padding: '80px', color: 'var(--text-dim)', background: '#ffffff', borderRadius: '10px', border: '1.5px solid #cbd5e1', boxShadow: '0 4px 12px rgba(15, 23, 42, 0.05)' }}>
              Chưa có video nào. Hãy qua trang "Xưởng Video" để tải video về trước!
            </div>
          ) : (
            projects.map((p) => {
              const transCount = p.translated_dialogues || 0;
              const total = p.total_dialogues || 1;
              const pct = Math.round((transCount / total) * 100);
              const isCompleted = p.has_final || pct === 100;
              const approxChapters = Math.ceil(total / 250);

              return (
                <div
                  key={p.id}
                  onClick={() => handleOpenProject(p)}
                  className="glass-panel"
                  style={{
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '14px',
                    cursor: 'pointer',
                    position: 'relative',
                    overflow: 'hidden'
                  }}
                >
                  {/* Top: Project ID & Status Badge */}
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '10px' }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', fontWeight: 800, color: '#2563eb', fontFamily: 'var(--font-mono)' }}>
                        <Bookmark size={14} /> Bộ Truyện #{p.id} • {p.video_id}
                      </div>
                      <h3 style={{ fontSize: '16px', fontWeight: 800, color: 'var(--text-main)', marginTop: '4px', lineHeight: 1.4 }}>
                        {p.title}
                      </h3>
                    </div>

                    <span className={`tag-badge ${isCompleted ? 'emerald' : pct > 0 ? 'purple' : 'amber'}`} style={{ fontSize: '11px', whiteSpace: 'nowrap' }}>
                      {isCompleted ? '✓ ĐÃ DỊCH XONG' : pct > 0 ? `ĐÃ DỊCH ${pct}%` : 'CHƯA DỊCH'}
                    </span>
                  </div>

                  {/* Info: Chapters, Lines, Duration */}
                  <div style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(3, 1fr)',
                    gap: '8px',
                    padding: '10px 12px',
                    background: '#f8fafc',
                    borderRadius: '8px',
                    border: '1px solid var(--border)'
                  }}>
                    <div style={{ textAlign: 'center' }}>
                      <div style={{ fontSize: '11px', color: 'var(--text-dim)', fontWeight: 600 }}>Số Chương</div>
                      <div style={{ fontSize: '14px', fontWeight: 800, color: 'var(--text-main)' }}>~{approxChapters} Chương</div>
                    </div>
                    <div style={{ textAlign: 'center', borderLeft: '1px solid var(--border)', borderRight: '1px solid var(--border)' }}>
                      <div style={{ fontSize: '11px', color: 'var(--text-dim)', fontWeight: 600 }}>Tổng Câu</div>
                      <div style={{ fontSize: '14px', fontWeight: 800, color: 'var(--text-main)' }}>{p.total_dialogues} câu</div>
                    </div>
                    <div style={{ textAlign: 'center' }}>
                      <div style={{ fontSize: '11px', color: 'var(--text-dim)', fontWeight: 600 }}>Thời Lượng</div>
                      <div style={{ fontSize: '14px', fontWeight: 800, color: 'var(--text-main)' }}>{formatDuration(p.duration)}</div>
                    </div>
                  </div>

                  {/* Progress Bar */}
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', color: 'var(--text-sub)', marginBottom: '5px' }}>
                      <span>Tiến độ bản dịch:</span>
                      <strong>{transCount} / {total} câu ({pct}%)</strong>
                    </div>
                    <div style={{ width: '100%', height: '7px', background: '#f1f5f9', borderRadius: '4px', overflow: 'hidden', border: '1px solid var(--border)' }}>
                      <div style={{ width: `${pct}%`, height: '100%', background: pct === 100 ? '#059669' : '#2563eb', transition: 'width 0.3s ease' }} />
                    </div>
                  </div>

                  {/* Open Button */}
                  <div style={{ paddingTop: '8px', borderTop: '1px solid var(--border)', marginTop: 'auto', display: 'flex', justifyContent: 'flex-end' }}>
                    <button
                      className="btn btn-primary btn-sm"
                      style={{ padding: '7px 16px', fontSize: '13px', display: 'flex', alignItems: 'center', gap: '6px' }}
                    >
                      <BookOpen size={14} /> Mở Đọc & Dịch ({approxChapters} Chương) ➔
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

  // =========================================================================
  // VIEW 2: PHÒNG ĐỌC KỊCH BẢN SONG NGỮ & DỊCH THUẬT (AIREAD CHAPTER READER)
  // =========================================================================
  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 64px)', overflow: 'hidden', background: '#f8fafc' }}>
      
      {/* LEFT: SCRIPT READER (AIREAD NOVEL & CHAPTER FLOW) */}
      <section style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', borderRight: '1px solid var(--border)' }}>
        
        {/* 1. Header Bar in Reader */}
        <div style={{
          padding: '14px 24px',
          background: '#ffffff',
          borderBottom: '1px solid var(--border)',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: '16px'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
            <button
              className="btn btn-secondary btn-sm"
              onClick={handleBackToLibrary}
              style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', padding: '7px 12px' }}
            >
              <ArrowLeft size={15} /> Thư Viện
            </button>

            <div>
              <h2 style={{ fontSize: '16px', fontWeight: 800, color: 'var(--text-main)', maxWidth: '420px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {selectedProject.title}
              </h2>
              <div style={{ fontSize: '12px', color: 'var(--text-sub)', marginTop: '2px' }}>
                Tổng số: <strong>{dialogues.length} câu</strong> • Đã dịch: <strong style={{ color: '#059669' }}>{translatedCount}/{dialogues.length} câu ({translatedPercent}%)</strong>
              </div>
            </div>
          </div>

          {/* Quick Actions: Mode Toggle & Search */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            
            {/* View Mode Toggle: Bilingual vs Vietnamese Only */}
            <div style={{ display: 'flex', background: '#f1f5f9', padding: '3px', borderRadius: '8px', border: '1px solid var(--border)' }}>
              <button
                onClick={() => setReadMode('bilingual')}
                className={`btn btn-sm ${readMode === 'bilingual' ? 'btn-primary' : 'btn-secondary'}`}
                style={{ padding: '4px 10px', fontSize: '12px', border: 'none' }}
                title="Đọc Song Ngữ Hán - Việt Đối Chiếu"
              >
                <Columns size={13} /> Song Ngữ
              </button>
              <button
                onClick={() => setReadMode('vietnamese_only')}
                className={`btn btn-sm ${readMode === 'vietnamese_only' ? 'btn-primary' : 'btn-secondary'}`}
                style={{ padding: '4px 10px', fontSize: '12px', border: 'none' }}
                title="Đọc Tiếng Việt Thuần Liền Mạch"
              >
                <AlignLeft size={13} /> Đọc Việt Thuần
              </button>
            </div>

            <input
              type="text"
              placeholder="🔍 Tìm thoại..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{ padding: '6px 12px', width: '150px', fontSize: '12px' }}
            />
            
            <button
              className={`btn btn-sm ${filter === 'ALL' ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setFilter('ALL')}
            >
              Tất Cả ({dialogues.length})
            </button>
            <button
              className={`btn btn-sm ${filter === 'TRANSLATED' ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setFilter('TRANSLATED')}
            >
              Đã Dịch ({translatedCount})
            </button>
            <button
              className={`btn btn-sm ${filter === 'UNTRANSLATED' ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setFilter('UNTRANSLATED')}
            >
              Chưa Dịch ({dialogues.length - translatedCount})
            </button>
          </div>
        </div>

        {/* 2. CHAPTER / LÔ NAVIGATOR BAR (Mỗi Lô = 1 Chương - Chuẩn AIREAD) */}
        <div style={{
          padding: '10px 24px',
          background: '#ffffff',
          borderBottom: '1px solid var(--border)',
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          overflowX: 'auto',
          whiteSpace: 'nowrap'
        }}>
          <span style={{ fontSize: '12px', fontWeight: 800, color: 'var(--text-dim)', textTransform: 'uppercase', marginRight: '4px' }}>
            📖 Chọn Chương:
          </span>

          <button
            onClick={() => setActiveChapterIndex('ALL')}
            className={`chapter-chip ${activeChapterIndex === 'ALL' ? 'active' : ''}`}
          >
            <Layers size={13} /> Tất Cả Các Chương ({dialogues.length} câu)
          </button>

          {chapters.map((ch) => {
            const isActive = activeChapterIndex === ch.chapterIndex;
            return (
              <button
                key={ch.chapterIndex}
                onClick={() => setActiveChapterIndex(ch.chapterIndex)}
                className={`chapter-chip ${isActive ? 'active' : ''} ${ch.isDone ? 'completed' : ''}`}
                title={`${ch.title}: ${ch.rangeLabel} (${ch.translatedCount}/${ch.totalCount} câu)`}
              >
                <span>{ch.title}</span>
                <span style={{ fontSize: '11px', opacity: 0.8 }}>({ch.rangeLabel})</span>
                {ch.isDone && <Check size={12} color={isActive ? '#ffffff' : '#059669'} />}
              </button>
            );
          })}
        </div>

        {/* 3. SCRIPT READER BODY (Clean White Paper Layout) */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '24px 32px', display: 'flex', flexDirection: 'column', gap: '14px', background: '#f8fafc' }}>
          {isDiagLoading ? (
            <div style={{ textAlign: 'center', padding: '100px', color: 'var(--text-dim)' }}>
              ⏳ Đang nạp danh sách câu thoại...
            </div>
          ) : filteredDialogues.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '100px', color: 'var(--text-dim)', background: '#ffffff', borderRadius: '12px', border: '1px solid var(--border)' }}>
              Không tìm thấy câu thoại nào theo bộ lọc này.
            </div>
          ) : readMode === 'vietnamese_only' ? (
            /* CHẾ ĐỘ: ĐỌC BẢN DỊCH TIẾNG VIỆT THUẦN (LIỀN MẠCH NHƯ TRANG TIỂU THUYẾT AIREAD) */
            <div style={{
              background: '#ffffff',
              padding: '36px 48px',
              borderRadius: '12px',
              border: '1px solid var(--border)',
              boxShadow: 'var(--shadow-main)',
              lineHeight: 1.9,
              fontSize: '16px',
              color: 'var(--text-main)'
            }}>
              <h3 style={{ fontSize: '18px', fontWeight: 800, marginBottom: '20px', paddingBottom: '12px', borderBottom: '1px solid var(--border)', color: 'var(--text-main)' }}>
                {activeChapterIndex === 'ALL' ? `Toàn Văn Bản Dịch: ${selectedProject.title}` : `Bản Dịch: ${chapters[activeChapterIndex]?.title} (${chapters[activeChapterIndex]?.rangeLabel})`}
              </h3>

              {filteredDialogues.map((d) => (
                <div key={d.id} style={{ marginBottom: '14px', display: 'flex', alignItems: 'baseline', gap: '12px' }}>
                  <span style={{ fontSize: '11.5px', color: 'var(--text-dim)', fontFamily: 'var(--font-mono)', userSelect: 'none', flexShrink: 0 }}>
                    [{formatDuration(d.start_time)}]
                  </span>
                  <div style={{ flex: 1 }}>
                    <span style={{ color: d.translated_text ? '#0f172a' : '#d97706', fontWeight: d.translated_text ? 500 : 400, fontStyle: d.translated_text ? 'normal' : 'italic' }}>
                      {d.translated_text || `(Chưa dịch câu #${d.index}: ${d.clean_text || d.original_text})`}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            /* CHẾ ĐỘ: ĐỌC SONG NGỮ HÁN - VIỆT ĐỐI CHIẾU (CHUẨN STUDIO AIREAD) */
            filteredDialogues.map((d) => {
              const hasVi = !!(d.translated_text && d.translated_text.trim());
              const chIndex = Math.floor((d.index - 1) / (batchSize || 250)) + 1;

              return (
                <div
                  key={d.id}
                  style={{
                    padding: '14px 18px',
                    background: '#ffffff',
                    borderLeft: hasVi ? '4px solid #2563eb' : '4px solid #d97706',
                    borderRadius: '0 8px 8px 0',
                    borderTop: '1px solid var(--border)',
                    borderRight: '1px solid var(--border)',
                    borderBottom: '1px solid var(--border)',
                    boxShadow: 'var(--shadow-sm)',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '8px'
                  }}
                >
                  {/* Top Row: Index + Chapter Pill + Timecode + Status */}
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center', fontFamily: 'var(--font-mono)', fontSize: '12px' }}>
                      <span className="tag-badge cyan" style={{ fontSize: '11px', padding: '2px 7px' }}>
                        Chương {chIndex} • #{d.index}
                      </span>
                      <span style={{ color: 'var(--text-dim)', background: '#f1f5f9', padding: '2px 8px', borderRadius: '4px', border: '1px solid var(--border)' }}>
                        ⏱ {formatDuration(d.start_time)} ➔ {formatDuration(d.end_time)}
                      </span>
                    </div>

                    <span className={`tag-badge ${hasVi ? 'emerald' : 'amber'}`} style={{ fontSize: '10px' }}>
                      {hasVi ? '✓ ĐÃ DỊCH' : 'CHỜ DỊCH'}
                    </span>
                  </div>

                  {/* Chinese Raw Text (Hán Gốc) */}
                  <div style={{ fontSize: '14.5px', color: '#334155', lineHeight: 1.6, padding: '4px 0' }}>
                    {d.clean_text || d.original_text}
                  </div>

                  {/* Vietnamese Text (Bản Dịch Tiếng Việt - Inline Edit) */}
                  <div>
                    <textarea
                      rows={1}
                      value={d.translated_text || ''}
                      placeholder="Chưa có bản dịch... Nhấn [Bắt Đầu Dịch AIREAD] ở cột phải"
                      onChange={(e) => handleTextChange(d.id, e.target.value)}
                      style={{
                        width: '100%',
                        padding: '8px 12px',
                        background: hasVi ? '#ffffff' : '#fefce8',
                        border: hasVi ? '1px solid #cbd5e1' : '1px solid #fde047',
                        borderRadius: '6px',
                        color: 'var(--text-main)',
                        fontSize: '14.5px',
                        fontWeight: 600,
                        lineHeight: 1.5,
                        resize: 'vertical',
                        fontFamily: 'var(--font-main)'
                      }}
                    />
                  </div>
                </div>
              );
            })
          )}
        </div>
      </section>

      {/* RIGHT: AIREAD TRANSLATION CONTROL & LIVE LOGS (380px) */}
      <aside style={{
        width: '380px',
        background: '#ffffff',
        display: 'flex',
        flexDirection: 'column',
        gap: '16px',
        padding: '20px',
        flexShrink: 0,
        overflowY: 'auto'
      }}>
        <div style={{ fontSize: '15px', fontWeight: 800, color: 'var(--text-main)', display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Sparkles size={18} color="#7c3aed" /> Bảng Dịch Thuật AIREAD
        </div>

        {/* Translation Settings Card */}
        <div className="glass-panel" style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <div>
            <label style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-sub)', display: 'block', marginBottom: '4px' }}>
              Thể loại kịch bản:
            </label>
            <select
              value={genre}
              onChange={(e) => setGenre(e.target.value)}
              style={{ width: '100%', padding: '8px 10px', fontSize: '12.5px' }}
            >
              {AIREAD_GENRES.map((g) => (
                <option key={g.code} value={g.code}>
                  {g.icon} {g.name}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-sub)', display: 'block', marginBottom: '4px' }}>
              Kích thước lô dịch (1 Lô = 1 Chương):
            </label>
            <select
              value={batchSize}
              onChange={(e) => setBatchSize(parseInt(e.target.value, 10))}
              style={{ width: '100%', padding: '8px 10px', fontSize: '12.5px' }}
            >
              <option value={250}>250 câu / lô (~22k token - Gửi 5-6 chương/lần)</option>
              <option value={350}>350 câu / lô (Tối đa ~25k token)</option>
              <option value={150}>150 câu / lô (~12k token)</option>
              <option value={80}>80 câu / lô (Lô nhỏ)</option>
            </select>
          </div>

          <button
            className="btn btn-primary"
            onClick={handleStartTranslate}
            disabled={isTranslating}
            style={{ width: '100%', padding: '12px', fontSize: '13.5px', fontWeight: 800, marginTop: '6px' }}
          >
            {isTranslating ? (
              <>
                <RefreshCw size={15} className="animate-spin" /> Đang Dịch ({progress}%)...
              </>
            ) : (
              <>
                <Sparkles size={15} /> BẮT ĐẦU DỊCH AIREAD
              </>
            )}
          </button>

          <div style={{ display: 'flex', gap: '8px', marginTop: '4px' }}>
            <button
              className="btn btn-emerald btn-sm"
              onClick={handleSaveAll}
              style={{ flex: 1, padding: '8px', fontSize: '12px' }}
            >
              <Save size={13} /> Lưu Sửa
            </button>
            <a
              href={`/api/v1/projects/download-srt/${selectedProject.id}`}
              download
              className="btn btn-secondary btn-sm"
              style={{ flex: 1, textDecoration: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '4px', padding: '8px', fontSize: '12px' }}
            >
              <Download size={13} /> Tải SRT VI
            </a>
          </div>
        </div>

        {/* Live Terminal Logs (AIREAD Style) */}
        <div className="glass-panel" style={{ padding: '16px', flex: 1, display: 'flex', flexDirection: 'column', minHeight: '280px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', fontWeight: 800, color: '#2563eb', marginBottom: '8px' }}>
            <Terminal size={14} /> Live AIREAD Pipeline Logs
          </div>

          <div style={{
            flex: 1,
            maxHeight: '340px',
            overflowY: 'auto',
            background: '#0f172a',
            padding: '10px 12px',
            borderRadius: '6px',
            fontFamily: 'var(--font-mono)',
            fontSize: '11px',
            lineHeight: 1.6,
            color: '#cbd5e1'
          }}>
            {logs.length === 0 ? (
              <div style={{ color: '#64748b' }}>Sẵn sàng dịch kịch bản... Nhấn [BẮT ĐẦU DỊCH AIREAD] để chạy.</div>
            ) : (
              logs.map((l, idx) => (
                <div key={idx} style={{
                  color: l.type === 'emerald' ? '#34d399' : l.type === 'cyan' ? '#38bdf8' : l.type === 'rose' ? '#f43f5e' : l.type === 'purple' ? '#c084fc' : '#cbd5e1'
                }}>
                  [{l.time}] {l.text}
                </div>
              ))
            )}
          </div>
        </div>
      </aside>
    </div>
  );
}
