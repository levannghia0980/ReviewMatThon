import React, { useState, useEffect, useMemo } from 'react';
import { 
  BookOpen, Sparkles, Save, Download, Search, 
  RefreshCw, CheckCircle2, AlertTriangle, Play, FileText, 
  Terminal, ArrowLeft, Clock, Film, Trash2, Edit3, AlignLeft, Columns,
  ChevronRight, Layers, Bookmark, Check, Volume2, RotateCcw
} from 'lucide-react';
import { AIREAD_GENRES, DEFAULT_GENRE } from '../constants/genres';

export default function DialogueLibraryView() {
  const [projects, setProjects] = useState([]);
  const [selectedProject, setSelectedProject] = useState(null);
  const [dialogues, setDialogues] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isDiagLoading, setIsDiagLoading] = useState(false);

  // Filters in Reader
  const [filter, setFilter] = useState('ALL'); // ALL, TRANSLATED
  const [readMode, setReadMode] = useState('bilingual'); // 'bilingual' | 'vietnamese_only'
  const [activeChapterIndex, setActiveChapterIndex] = useState('ALL'); // 'ALL' | number (0, 1, 2...)

  // Editing & Saving State
  const [unsavedIds, setUnsavedIds] = useState(new Set());
  const [savingIds, setSavingIds] = useState(new Set());
  const [isSavingAll, setIsSavingAll] = useState(false);

  // AIREAD Translation Engine Settings
  const [genre, setGenre] = useState(DEFAULT_GENRE);
  const [batchSize, setBatchSize] = useState(500); // Default large batch (Mỗi lô = 1 chương lớn)
  
  // Translation Task State
  const [isTranslating, setIsTranslating] = useState(false);
  const [progress, setProgress] = useState(0);
  const [taskId, setTaskId] = useState(null);
  const [logs, setLogs] = useState([]);
  
  // STT Extraction Task State
  const [isExtracting, setIsExtracting] = useState(false);
  const [extractTaskId, setExtractTaskId] = useState(null);


  useEffect(() => {
    loadProjects();
    fetch('/api/v1/settings')
      .then(r => r.json())
      .then(d => {
        if (d.translation_batch_size) setBatchSize(d.translation_batch_size);
      })
      .catch(() => {});

    const handleSettingsUpdated = (e) => {
      const d = e?.detail;
      if (d && d.translation_batch_size) {
        setBatchSize(d.translation_batch_size);
      }
    };
    window.addEventListener('settings-updated', handleSettingsUpdated);
    return () => window.removeEventListener('settings-updated', handleSettingsUpdated);
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

  const [batchesData, setBatchesData] = useState({ total_items: 0, total_translated: 0, batches: [] });
  const [activeBatchIndex, setActiveBatchIndex] = useState(0); // 0 = Lô 1

  // Nạp danh mục Lô (Chương) siêu nhẹ chuẩn AIREAD
  const loadBatches = async (projectId, targetBatchIndex = 0) => {
    try {
      const res = await fetch(`/api/v1/projects/${projectId}/batches?batch_size=${batchSize}`);
      if (res.ok) {
        const data = await res.json();
        setBatchesData(data);
        if (data.batches && data.batches.length > 0) {
          const validIndex = Math.min(Math.max(0, targetBatchIndex), data.batches.length - 1);
          setActiveBatchIndex(validIndex);
          await fetchBatchDialogues(projectId, validIndex);
        } else {
          setDialogues([]);
        }
      }
    } catch (e) {
      console.error("Lỗi khi nạp batches:", e);
    }
  };

  // Nạp câu thoại của ĐÚNG 1 LÔ (Ưu tiên nạp câu gộp kịch bản TTS chuẩn nhân vật)
  const fetchBatchDialogues = async (projectId, bIndex) => {
    setIsDiagLoading(true);
    setUnsavedIds(new Set());
    try {
      // 1. Thử nạp từ bảng câu gộp TTS (có nhân vật & thời lượng lồng tiếng)
      const ttsRes = await fetch(`/api/v1/projects/${projectId}/tts-dialogues${bIndex !== undefined ? `?batch_index=${bIndex + 1}` : ''}`);
      if (ttsRes.ok) {
        const ttsData = await ttsRes.json();
        if (ttsData.items && ttsData.items.length > 0) {
          const normalized = ttsData.items.map(t => ({
            ...t,
            index: t.tts_index,
            text: t.original_text,
            clean_text: t.original_text,
            translated_text: t.translated_text,
            role: t.speaker || 'DẪN CHUYỆN',
            speaker: t.speaker || 'DẪN CHUYỆN'
          }));
          setDialogues(normalized);
          setIsDiagLoading(false);
          return;
        }
      }

      // 2. Fallback nạp từ danh sách câu thoại ASR gốc
      const res = await fetch(`/api/v1/projects/${projectId}/dialogues?batch_index=${bIndex}&page_size=${batchSize}`);
      if (res.ok) {
        const data = await res.json();
        setDialogues(data.items || []);
      }
    } catch (e) {
      console.error("Lỗi khi nạp câu thoại theo lô:", e);
    } finally {
      setIsDiagLoading(false);
    }
  };

  const handleSelectBatch = async (bIndex) => {
    if (bIndex === activeBatchIndex || !selectedProject) return;
    setActiveBatchIndex(bIndex);
    await fetchBatchDialogues(selectedProject.id, bIndex);
  };

  const handleOpenProject = async (project) => {
    setSelectedProject(project);
    setActiveBatchIndex(0);
    setIsDiagLoading(true);
    setUnsavedIds(new Set());
    await loadBatches(project.id, 0);
    setIsDiagLoading(false);
  };

  const handleBackToLibrary = () => {
    setSelectedProject(null);
    setDialogues([]);
    setUnsavedIds(new Set());
    setBatchesData({ total_items: 0, total_translated: 0, batches: [] });
    loadProjects();
  };

  // Lọc câu thoại trong Lô đang chọn theo Trạng thái
  const filteredDialogues = useMemo(() => {
    let list = dialogues;
    if (filter === 'TRANSLATED') {
      list = list.filter(d => ['TRANSLATED', 'SILENT_VALID', 'DUBBED'].includes(d.status) || (d.translated_text !== null && d.translated_text !== undefined));
    }
    return list;
  }, [dialogues, filter]);

  const currentBatch = useMemo(() => {
    if (!batchesData.batches || batchesData.batches.length === 0) return null;
    return batchesData.batches[activeBatchIndex] || batchesData.batches[0];
  }, [batchesData.batches, activeBatchIndex]);

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

          if (['completed', 'DONE', 'success', 'SUCCESS'].includes(data.status)) {
            setIsTranslating(false);
            clearInterval(interval);
            if (selectedProject) {
              await loadBatches(selectedProject.id, activeBatchIndex);
            }
            loadProjects();
          } else if (data.status === 'cancelled') {
            setIsTranslating(false);
            clearInterval(interval);
          } else if (data.status === 'failed' || data.status === 'FAILED') {
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
  }, [isTranslating, taskId, selectedProject?.id, activeBatchIndex]);

  // Poll STT Extraction Task
  useEffect(() => {
    let interval = null;
    if (isExtracting && extractTaskId) {
      interval = setInterval(async () => {
        try {
          const res = await fetch(`/api/v1/pipeline/status/${extractTaskId}`);
          if (!res.ok) return;
          const data = await res.json();
          setProgress(data.progress || 0);
          if (data.logs) setLogs(data.logs);

          if (['completed', 'DONE', 'success', 'SUCCESS'].includes(data.status)) {
            setIsExtracting(false);
            clearInterval(interval);
            if (selectedProject) {
              await loadBatches(selectedProject.id, 0);
            }
            loadProjects();
          } else if (data.status === 'failed' || data.status === 'FAILED') {
            setIsExtracting(false);
            clearInterval(interval);
            alert(`Lỗi khi bóc thoại: ${data.error}`);
          }
        } catch (e) {
          console.error(e);
        }
      }, 2000);
    }
    return () => clearInterval(interval);
  }, [isExtracting, extractTaskId, selectedProject, activeBatchIndex]);

  const handleExtractSTT = async () => {
    if (!selectedProject) return;
    setIsExtracting(true);
    setProgress(5);
    setLogs([]);

    try {
      const res = await fetch('/api/v1/pipeline/extract-stt', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_id: selectedProject.id, source_language: selectedProject.source_language || 'zh' })
      });
      if (res.ok) {
        const data = await res.json();
        setExtractTaskId(data.task_id);
      } else {
        setIsExtracting(false);
        alert('Lỗi khởi chạy tiến trình bóc thoại');
      }
    } catch (e) {
      setIsExtracting(false);
      console.error(e);
    }
  };

  const handleCancelTranslate = async () => {
    if (!taskId) {
      setIsTranslating(false);
      return;
    }
    try {
      await fetch(`/api/v1/translate/cancel/${taskId}`, { method: 'POST' });
      setIsTranslating(false);
      setLogs(prev => [...prev, { time: new Date().toLocaleTimeString(), text: '🛑 Đã dừng tiến trình dịch thuật! Dữ liệu các lô đã dịch xong được bảo toàn nguyên vẹn.', type: 'amber' }]);
      if (selectedProject) {
        await loadBatches(selectedProject.id, activeBatchIndex);
      }
      loadProjects();
    } catch (e) {
      console.error(e);
      setIsTranslating(false);
    }
  };

  const handleStartTranslate = async () => {
    if (!selectedProject) return;
    setIsTranslating(true);
    setProgress(5);
    setLogs([{ time: new Date().toLocaleTimeString(), text: `🚀 Bắt đầu tiến trình dịch thuật cho bộ truyện #${selectedProject.id} (${selectedProject.title})...`, type: 'cyan' }]);

    try {
      const res = await fetch('/api/v1/translate/project', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: selectedProject.id,
          genre: genre,
          batch_size: batchSize,
          max_chars: 22000
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
    setUnsavedIds(prev => new Set(prev).add(id));
  };

  const handleSaveSingle = async (id) => {
    const diag = dialogues.find(d => d.id === id);
    if (!diag || !selectedProject) return;
    setSavingIds(prev => new Set(prev).add(id));
    try {
      const res = await fetch(`/api/v1/projects/${selectedProject.id}/dialogues/update`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          items: [{
            id: diag.id,
            translated_text: diag.translated_text || '',
            speaker: diag.speaker || diag.role || null
          }]
        })
      });
      if (res.ok) {
        setUnsavedIds(prev => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
      }
    } catch (err) {
      console.error("Lỗi tự động lưu câu:", err);
    } finally {
      setSavingIds(prev => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  };

  const handleSaveAll = async () => {
    if (!selectedProject || dialogues.length === 0) return;
    setIsSavingAll(true);
    try {
      const targetDialogues = unsavedIds.size > 0 
        ? dialogues.filter(d => unsavedIds.has(d.id))
        : dialogues;

      const payload = {
        items: targetDialogues.map(d => ({
          id: d.id,
          translated_text: d.translated_text || '',
          speaker: d.speaker || d.role || null
        }))
      };

      const res = await fetch(`/api/v1/projects/${selectedProject.id}/dialogues/update`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (res.ok) {
        setUnsavedIds(new Set());
        alert(data.message || `✔ Đã lưu thành công ${targetDialogues.length} câu thoại vào Database SQLite!`);
      } else {
        alert(`Lỗi khi lưu: ${data.detail || 'Không thể lưu'}`);
      }
    } catch (err) {
      alert(`Lỗi kết nối: ${err.message}`);
    } finally {
      setIsSavingAll(false);
    }
  };

  const handleResetTranslation = async () => {
    if (!selectedProject) return;
    if (!window.confirm(`⚠️ BẠN CÓ CHẮC MUỐN XÓA SẠCH BẢN DỊCH ĐỂ DỊCH LẠI?\n\n"${selectedProject.title}" (ID: #${selectedProject.id})\n\nThao tác này sẽ:\n✓ GIỮ NGUYÊN video gốc và toàn bộ câu thoại tiếng Trung gốc\n✗ XÓA SẠCH toàn bộ kịch bản dịch tiếng Việt cả trên máy và Database\n✗ Xóa sạch toàn bộ audio thuyết minh và video render\n\nBạn có thể bấm "Bắt Đầu Dịch" ngay lập tức để AI dịch lại từ đầu mà không cần bóc tách lại thoại!`)) {
      return;
    }

    try {
      const res = await fetch(`/api/v1/projects/${selectedProject.id}/reset-translation`, { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        alert(data.message || 'Đã reset sạch toàn bộ bản dịch tiếng Việt thành công!');
        await loadBatches(selectedProject.id, 0);
        loadProjects();
      } else {
        alert(`Lỗi khi reset bản dịch: ${data.detail || 'Không thể reset'}`);
      }
    } catch (err) {
      alert(`Lỗi kết nối: ${err.message}`);
    }
  };

  const handleResetToDownloaded = async () => {
    if (!selectedProject) return;
    if (!window.confirm(`⚠️ BẠN CÓ CHẮC MUỐN RESET VỀ LÚC MỚI TẢI XONG?\n\n"${selectedProject.title}" (ID: #${selectedProject.id})\n\nThao tác này sẽ:\n✓ Giữ nguyên file video gốc\n✗ XÓA SẠCH TOÀN BỘ CÂU THOẠI & BẢN DỊCH CẢ TRÊN MÁY VÀ DATABASE\n✗ Xóa sạch toàn bộ audio thuyết minh và video render\n\nDự án sẽ trở về trạng thái như vừa tải video xong!`)) {
      return;
    }

    try {
      const res = await fetch(`/api/v1/projects/${selectedProject.id}/reset`, { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        alert(data.message || 'Đã reset về lúc mới tải xong thành công!');
        setDialogues([]);
        setBatchesData({ total_items: 0, total_translated: 0, batches: [] });
        loadProjects();
        setSelectedProject(prev => prev ? { ...prev, status: 'DOWNLOADED', total_dialogues: 0 } : null);
      } else {
        alert(`Lỗi khi reset: ${data.detail || 'Không thể reset'}`);
      }
    } catch (err) {
      alert(`Lỗi kết nối: ${err.message}`);
    }
  };

  const formatDuration = (sec) => {
    if (!sec) return '00:00';
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  };

  // Tổng câu và số câu đã dịch toàn bộ phim (Dựa trên metadata từ batchesData chuẩn chỉ, không phụ thuộc vào Lô hiện tại)
  const totalDialoguesAll = batchesData.total_items || (selectedProject ? selectedProject.total_dialogues : 0) || 0;
  const translatedCountAll = batchesData.total_translated || (selectedProject ? selectedProject.translated_dialogues : 0) || 0;
  const translatedPercentAll = totalDialoguesAll > 0 ? Math.round((translatedCountAll / totalDialoguesAll) * 100) : 0;

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
                Toàn bộ phim: <strong>{totalDialoguesAll} câu ASR gốc</strong> • Đã dịch: <strong style={{ color: '#059669' }}>{translatedCountAll}/{totalDialoguesAll} câu ({translatedPercentAll}%)</strong>
                {currentBatch && (
                  <span style={{ marginLeft: '10px', background: '#e0e7ff', color: '#4338ca', padding: '2px 8px', borderRadius: '4px', fontWeight: 700 }}>
                    Kịch bản lồng tiếng: {dialogues.length} câu đã gộp ({currentBatch.title} • {currentBatch.range_label})
                  </span>
                )}
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
                title="Đọc Song Ngữ Hán - Việt Đối Chiối"
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

            <button
              className={`btn btn-sm ${filter === 'ALL' ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setFilter('ALL')}
            >
              Lô Này ({dialogues.length})
            </button>
            <button
              className={`btn btn-sm ${filter === 'TRANSLATED' ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setFilter('TRANSLATED')}
            >
              Đã Dịch ({dialogues.filter(d => ['TRANSLATED', 'SILENT_VALID', 'DUBBED'].includes(d.status) || (d.translated_text !== null && d.translated_text !== undefined)).length})
            </button>
            <button
              className="btn btn-sm"
              onClick={handleSaveAll}
              disabled={isSavingAll}
              style={{
                background: unsavedIds.size > 0 ? '#10b981' : '#f0fdf4',
                color: unsavedIds.size > 0 ? '#ffffff' : '#15803d',
                border: '1.5px solid ' + (unsavedIds.size > 0 ? '#059669' : '#86efac'),
                display: 'flex',
                alignItems: 'center',
                gap: '5px',
                fontWeight: 700,
                padding: '5px 12px',
                fontSize: '12px',
                cursor: isSavingAll ? 'not-allowed' : 'pointer',
                boxShadow: unsavedIds.size > 0 ? '0 2px 8px rgba(16, 185, 129, 0.35)' : 'none',
                transition: 'all 0.2s ease'
              }}
              title="Lưu tất cả thay đổi kịch bản của lô này vào Database SQLite"
            >
              <Save size={13} />
              {isSavingAll 
                ? 'Đang lưu...' 
                : unsavedIds.size > 0 
                  ? `Lưu Sửa Đổi (${unsavedIds.size})` 
                  : 'Lưu Kịch Bản'}
            </button>

            <button
              className="btn btn-sm"
              onClick={handleResetTranslation}
              style={{
                background: '#fef3c7',
                color: '#b45309',
                border: '1px solid #fde68a',
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
                fontWeight: 700,
                padding: '5px 10px',
                fontSize: '12px'
              }}
              title="Xóa sạch toàn bộ bản dịch tiếng Việt cả trên máy và DB (giữ nguyên câu gốc tiếng Trung để dịch lại ngay)"
            >
              <RotateCcw size={12} /> Reset Bản Dịch
            </button>

            <button
              className="btn btn-sm"
              onClick={handleResetToDownloaded}
              style={{
                background: '#fee2e2',
                color: '#dc2626',
                border: '1px solid #fca5a5',
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
                fontWeight: 700,
                padding: '5px 10px',
                fontSize: '12px'
              }}
              title="Reset về lúc mới tải xong (xóa toàn bộ câu thoại và bản dịch cả trên máy và DB)"
            >
              <Trash2 size={12} /> Reset Mới Tải
            </button>
          </div>
        </div>

        {/* 2. BREADCRUMB / MINI LÔ NAVIGATOR BAR */}
        <div style={{
          padding: '8px 24px',
          background: '#ffffff',
          borderBottom: '1px solid var(--border)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '8px'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span style={{ fontSize: '12px', fontWeight: 800, color: 'var(--text-dim)', textTransform: 'uppercase' }}>
              📦 Đang xem:
            </span>
            {currentBatch ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span style={{
                  fontSize: '13px',
                  fontWeight: 800,
                  color: '#4338ca',
                  background: '#e0e7ff',
                  padding: '3px 10px',
                  borderRadius: '6px'
                }}>
                  {currentBatch.title} ({currentBatch.range_label})
                </span>
                <span style={{ fontSize: '12px', color: 'var(--text-sub)' }}>
                  Trạng thái: <strong style={{ color: currentBatch.is_done ? '#059669' : currentBatch.translated > 0 ? '#d97706' : '#64748b' }}>
                    {currentBatch.is_done ? '✔ Đã dịch xong' : currentBatch.translated > 0 ? `⏳ Đang dịch dở (${currentBatch.translated}/${currentBatch.total})` : '⚪ Chưa dịch'}
                  </strong>
                </span>
              </div>
            ) : (
              <span style={{ fontSize: '12px', color: 'var(--text-sub)' }}>Chưa có lô nào</span>
            )}
          </div>

          {/* Nút chuyển Lô nhanh Trước / Sau */}
          {batchesData.batches && batchesData.batches.length > 1 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <button
                className="btn btn-secondary btn-sm"
                disabled={activeBatchIndex === 0}
                onClick={() => handleSelectBatch(activeBatchIndex - 1)}
                style={{ padding: '4px 8px', fontSize: '11px', fontWeight: 700 }}
              >
                ◀ Lô trước
              </button>
              <span style={{ fontSize: '11px', fontWeight: 700, color: 'var(--text-dim)' }}>
                {activeBatchIndex + 1} / {batchesData.batches.length}
              </span>
              <button
                className="btn btn-secondary btn-sm"
                disabled={activeBatchIndex >= batchesData.batches.length - 1}
                onClick={() => handleSelectBatch(activeBatchIndex + 1)}
                style={{ padding: '4px 8px', fontSize: '11px', fontWeight: 700 }}
              >
                Lô sau ▶
              </button>
            </div>
          )}
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
            filteredDialogues.map((d, dIdx) => {
              const isMergedChild = d.status === 'MERGED_CHILD' || (d.translated_text === '' && d.duration === 0);
              const isUntranslated = d.translated_text === null || d.translated_text === undefined;
              const hasVi = !isUntranslated && !isMergedChild && !!d.translated_text.trim();
              const isSilent = !isUntranslated && !hasVi && !isMergedChild;
              const chIndex = Math.floor((d.index - 1) / (batchSize || 250)) + 1;

              // Tìm xem câu này gộp bao nhiêu câu sau (nếu các câu liền sau là MERGED_CHILD)
              let mergedCount = 1;
              if (hasVi) {
                for (let nextI = dIdx + 1; nextI < filteredDialogues.length; nextI++) {
                  const nextD = filteredDialogues[nextI];
                  if (nextD.status === 'MERGED_CHILD' || (nextD.translated_text === '' && nextD.duration === 0)) {
                    mergedCount++;
                  } else {
                    break;
                  }
                }
              }

              // Nếu là câu con đã được gộp vào câu trước (khi chưa tách): Bỏ qua không render dòng xám rác
              if (isMergedChild) {
                return null;
              }


              return (
                <div
                  key={d.id}
                  style={{
                    padding: '14px 18px',
                    background: '#ffffff',
                    borderLeft: hasVi ? (mergedCount > 1 ? '5px solid #7c3aed' : '4px solid #2563eb') : isSilent ? '4px solid #94a3b8' : '4px solid #d97706',
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

                      {/* Huy hiệu phân vai minh bạch: HỆ THỐNG / DẪN CHUYỆN / NHÂN VẬT */}
                      {(d.speaker === 'HỆ THỐNG' || d.role === 'HỆ THỐNG' || d.speaker === 'SYSTEM' || d.role === 'SYSTEM') ? (
                        <span style={{ fontSize: '11px', padding: '2px 8px', fontWeight: 800, background: '#fef3c7', color: '#b45309', border: '1px solid #fcd34d', borderRadius: '4px', display: 'inline-flex', alignItems: 'center', gap: '3px' }}>
                          🔔 HỆ THỐNG
                        </span>
                      ) : (d.speaker === 'DẪN CHUYỆN' || d.role === 'DẪN CHUYỆN' || d.speaker === 'NARRATOR' || d.role === 'NARRATOR' || !d.speaker) ? (
                        <span style={{ fontSize: '11px', padding: '2px 8px', fontWeight: 800, background: '#fee2e2', color: '#000000', border: '1.5px solid #dc2626', borderRadius: '4px', display: 'inline-flex', alignItems: 'center', gap: '3px' }}>
                          🎙️ DẪN CHUYỆN
                        </span>
                      ) : (
                        <span className="tag-badge purple" style={{ fontSize: '11px', padding: '2px 8px', fontWeight: 700 }}>
                          👤 {d.speaker || d.role}
                        </span>
                      )}

                      <span style={{ color: 'var(--text-dim)', background: '#f1f5f9', padding: '2px 8px', borderRadius: '4px', border: '1px solid var(--border)' }}>
                        ⏱ {formatDuration(d.start_time)} ➔ {formatDuration(d.end_time)}
                      </span>

                      {/* Hiển thị chi tiết câu gộp thực tế từ bảng TTS */}
                      {(d.orig_count > 1 || (d.src_indices && d.src_indices.includes(','))) ? (
                        <span style={{
                          fontSize: '11px',
                          fontWeight: 800,
                          color: '#7c3aed',
                          background: '#f3e8ff',
                          border: '1px solid #d8b4fe',
                          padding: '2px 8px',
                          borderRadius: '4px',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '4px'
                        }}>
                          🔗 Gộp {d.orig_count || d.src_indices.split(',').length} câu ASR (#{d.src_start_index || d.src_indices.split(',')[0]} ➔ #{d.src_end_index || d.src_indices.split(',').slice(-1)[0]} | ⏱ {d.duration ? `${d.duration.toFixed(1)}s` : ''})
                        </span>
                      ) : (mergedCount > 1 ? (
                        <span style={{
                          fontSize: '11px',
                          fontWeight: 800,
                          color: '#7c3aed',
                          background: '#f3e8ff',
                          border: '1px solid #d8b4fe',
                          padding: '2px 8px',
                          borderRadius: '4px',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '4px'
                        }}>
                          🔗 Gộp {mergedCount} câu gốc (#{d.index} ➔ #{d.index + mergedCount - 1})
                        </span>
                      ) : null)}
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      {savingIds.has(d.id) ? (
                        <span style={{ fontSize: '10px', padding: '2px 7px', background: '#fef3c7', color: '#b45309', borderRadius: '4px', fontWeight: 700, display: 'inline-flex', alignItems: 'center', gap: '3px' }}>
                          ⏳ Đang lưu...
                        </span>
                      ) : unsavedIds.has(d.id) ? (
                        <span style={{ fontSize: '10px', padding: '2px 7px', background: '#fef3c7', color: '#b45309', border: '1px solid #fcd34d', borderRadius: '4px', fontWeight: 700, display: 'inline-flex', alignItems: 'center', gap: '3px' }}>
                          ✏️ Đã sửa (chưa lưu)
                        </span>
                      ) : null}

                      <span className={`tag-badge ${hasVi ? 'emerald' : isSilent ? 'gray' : 'amber'}`} style={{ fontSize: '10px' }}>
                        {hasVi ? ((d.orig_count > 1 || mergedCount > 1) ? `✓ ĐÃ DỊCH GỘP (${d.orig_count || mergedCount} CÂU)` : '✓ ĐÃ DỊCH') : isSilent ? '🔇 IM LẶNG / BỎ QUA' : 'CHỜ DỊCH'}
                      </span>
                    </div>
                  </div>

                  {/* Chinese Raw Text (Hán Gốc) */}
                  <div style={{ fontSize: '14.5px', color: '#334155', lineHeight: 1.6, padding: '4px 0', fontWeight: mergedCount > 1 ? 600 : 400 }}>
                    {d.clean_text || d.original_text}
                  </div>

                  {/* Vietnamese Text (Bản Dịch Tiếng Việt - Inline Edit) */}
                  <div>
                    <textarea
                      ref={(el) => {
                        if (el) {
                          el.style.height = 'auto';
                          el.style.height = `${Math.max(46, el.scrollHeight + 2)}px`;
                        }
                      }}
                      value={d.translated_text || ''}
                      placeholder={isSilent ? '(Đoạn này giữ im lặng / không tạo audio TTS)' : 'Chưa có bản dịch... Nhấn [Bắt Đầu Dịch AIREAD] ở cột phải'}
                      onChange={(e) => {
                        handleTextChange(d.id, e.target.value);
                        e.target.style.height = 'auto';
                        e.target.style.height = `${Math.max(46, e.target.scrollHeight + 2)}px`;
                      }}
                      onBlur={() => {
                        if (unsavedIds.has(d.id)) {
                          handleSaveSingle(d.id);
                        }
                      }}
                      style={{
                        width: '100%',
                        padding: '8px 12px',
                        background: hasVi ? '#ffffff' : isSilent ? '#f8fafc' : '#fefce8',
                        border: hasVi ? (mergedCount > 1 ? '1.5px solid #c084fc' : '1px solid #cbd5e1') : isSilent ? '1px dashed #cbd5e1' : '1px solid #fde047',
                        borderRadius: '6px',
                        color: 'var(--text-main)',
                        fontSize: '14.5px',
                        fontWeight: 600,
                        lineHeight: 1.5,
                        resize: 'vertical',
                        overflow: 'hidden',
                        fontFamily: 'var(--font-main)',
                        boxSizing: 'border-box'
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

        {/* KHUNG DANH SÁCH LÔ / CHƯƠNG VUÔNG NHỎ GỌN (CHUẨN WEB ĐỌC TRUYỆN AIREAD) */}
        {batchesData.batches && batchesData.batches.length > 0 && (
          <div className="glass-panel" style={{ padding: '14px', display: 'flex', flexDirection: 'column', gap: '10px', background: '#faf5ff', border: '1px solid #e9d5ff' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: '12.5px', fontWeight: 800, color: '#6b21a8', display: 'flex', alignItems: 'center', gap: '6px' }}>
                <Layers size={14} color="#7c3aed" /> Mục Lục Lô ({batchesData.batches.length} lô)
              </span>
              <span style={{ fontSize: '11px', color: '#7c3aed', fontWeight: 700 }}>
                {batchesData.total_translated}/{batchesData.total_items} câu ({translatedPercentAll}%)
              </span>
            </div>

            {/* Chú thích màu sắc */}
            <div style={{ display: 'flex', gap: '10px', fontSize: '10.5px', color: 'var(--text-dim)' }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: '3px' }}>
                <span style={{ width: '8px', height: '8px', borderRadius: '2px', background: '#10b981', display: 'inline-block' }}></span> Xong
              </span>
              <span style={{ display: 'flex', alignItems: 'center', gap: '3px' }}>
                <span style={{ width: '8px', height: '8px', borderRadius: '2px', background: '#f59e0b', display: 'inline-block' }}></span> Dở dang
              </span>
              <span style={{ display: 'flex', alignItems: 'center', gap: '3px' }}>
                <span style={{ width: '8px', height: '8px', borderRadius: '2px', background: '#cbd5e1', display: 'inline-block' }}></span> Chưa dịch
              </span>
            </div>

            {/* Grid các ô vuông nhỏ gọn dễ thao tác */}
            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(5, 1fr)',
              gap: '6px',
              maxHeight: '190px',
              overflowY: 'auto',
              padding: '4px',
              background: '#ffffff',
              borderRadius: '8px',
              border: '1px solid #f3e8ff'
            }}>
              {batchesData.batches.map((b) => {
                const isSelected = activeBatchIndex === b.batch_index;
                const isDone = b.is_done;
                const isPartial = !isDone && b.translated > 0;

                // Màu sắc trực quan
                let bg = '#f8fafc';
                let color = '#475569';
                let borderColor = '#e2e8f0';

                if (isDone) {
                  bg = isSelected ? '#059669' : '#ecfdf5';
                  color = isSelected ? '#ffffff' : '#047857';
                  borderColor = '#a7f3d0';
                } else if (isPartial) {
                  bg = isSelected ? '#d97706' : '#fffbeb';
                  color = isSelected ? '#ffffff' : '#b45309';
                  borderColor = '#fde68a';
                } else {
                  if (isSelected) {
                    bg = '#7c3aed';
                    color = '#ffffff';
                    borderColor = '#6d28d9';
                  }
                }

                return (
                  <button
                    key={b.batch_index}
                    onClick={() => handleSelectBatch(b.batch_index)}
                    title={`${b.title}: ${b.range_label} (${b.translated}/${b.total} câu) - ${b.status}`}
                    style={{
                      height: '36px',
                      display: 'flex',
                      flexDirection: 'column',
                      alignItems: 'center',
                      justifyContent: 'center',
                      background: bg,
                      color: color,
                      border: `1.5px solid ${isSelected ? (isDone ? '#047857' : isPartial ? '#b45309' : '#5b21b6') : borderColor}`,
                      borderRadius: '6px',
                      cursor: 'pointer',
                      fontSize: '11px',
                      fontWeight: 800,
                      position: 'relative',
                      boxShadow: isSelected ? '0 0 0 2px rgba(124, 58, 237, 0.3)' : 'none',
                      transition: 'all 0.15s ease'
                    }}
                  >
                    <span>#{b.batch_number}</span>
                    {isDone && <span style={{ fontSize: '8px', lineHeight: 1 }}>✓</span>}
                  </button>
                );
              })}
            </div>
            
            {currentBatch && (
              <div style={{ fontSize: '11px', color: '#6b21a8', textAlign: 'center', fontWeight: 600 }}>
                Đang xem: <strong>{currentBatch.title}</strong> ({currentBatch.range_label})
              </div>
            )}
          </div>
        )}

        {/* Extraction Status Card */}
        <div className="glass-panel" style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px', background: 'var(--bg-sub)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-sub)' }}>Trạng thái Lời Thoại:</span>
            {totalDialoguesAll > 0 ? (
              <span style={{ fontSize: '12px', fontWeight: 700, color: 'var(--emerald)', display: 'flex', alignItems: 'center', gap: '4px' }}>
                <CheckCircle2 size={14} /> Đã có ({totalDialoguesAll} câu)
              </span>
            ) : (
              <span style={{ fontSize: '12px', fontWeight: 700, color: 'var(--rose)', display: 'flex', alignItems: 'center', gap: '4px' }}>
                <AlertTriangle size={14} /> Chưa lấy
              </span>
            )}
          </div>
          
          {(!dialogues || dialogues.length === 0) && (
            <button
              className="btn btn-secondary"
              onClick={handleExtractSTT}
              disabled={isExtracting}
              style={{ width: '100%', padding: '10px', fontSize: '12.5px', fontWeight: 700 }}
            >
              {isExtracting ? (
                <><RefreshCw size={14} className="animate-spin" /> Đang bóc tách ({progress}%)...</>
              ) : (
                <><Volume2 size={14} /> Lấy Thoại Tự Động (AI STT)</>
              )}
            </button>
          )}
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

          {isTranslating ? (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: '8px', marginTop: '6px' }}>
              <button
                type="button"
                className="btn btn-primary"
                disabled
                style={{ width: '100%', padding: '12px', fontSize: '13px', fontWeight: 800, cursor: 'wait' }}
              >
                <RefreshCw size={15} className="animate-spin" /> Đang Dịch ({progress}%)...
              </button>
              <button
                type="button"
                className="btn"
                onClick={handleCancelTranslate}
                title="Dừng ngay lập tức quá trình dịch và giữ nguyên các lô đã hoàn thành"
                style={{
                  padding: '12px 14px',
                  fontSize: '12.5px',
                  fontWeight: 900,
                  background: '#dc2626',
                  color: '#ffffff',
                  border: '1px solid #b91c1c',
                  cursor: 'pointer',
                  borderRadius: '6px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px'
                }}
              >
                🛑 DỪNG
              </button>
            </div>
          ) : (
            <button
              className="btn btn-primary"
              onClick={handleStartTranslate}
              style={{ width: '100%', padding: '12px', fontSize: '13.5px', fontWeight: 800, marginTop: '6px' }}
            >
              <Sparkles size={15} /> BẮT ĐẦU DỊCH AIREAD
            </button>
          )}

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
