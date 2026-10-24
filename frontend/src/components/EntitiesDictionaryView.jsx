import React, { useState, useEffect, useMemo } from 'react';
import { 
  BookOpen, Plus, Trash2, Edit3, Sparkles, RefreshCw, 
  Search, X, Check, ArrowRight
} from 'lucide-react';

const CATEGORY_MAP = {
  ALL: 'Tất Cả Phân Loại',
  NAME: 'Nhân Vật (NAME)',
  SECT: 'Môn Phái (SECT)',
  PLACE: 'Địa Danh (PLACE)',
  ITEM: 'Bảo Vật / Đan Dược (ITEM)',
  SKILL: 'Võ Học / Công Pháp (SKILL)',
  CREATURE: 'Linh Thú (CREATURE)',
  OTHER: 'Cảnh Giới / Khác (OTHER)'
};

export default function EntitiesDictionaryView() {
  const [projects, setProjects] = useState([]);
  const [selectedProjectId, setSelectedProjectId] = useState(null);
  const [entities, setEntities] = useState([]);
  const [originalEntitiesMap, setOriginalEntitiesMap] = useState({});
  const [summary, setSummary] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isApplying, setIsApplying] = useState(false);

  // Search & Filter
  const [search, setSearch] = useState('');
  const [filterCategory, setFilterCategory] = useState('ALL');
  const [scope, setScope] = useState('ALL');

  // Modal State (Add / Edit Entity)
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingIndex, setEditingIndex] = useState(null);
  const [modalChName, setModalChName] = useState('');
  const [modalViName, setModalViName] = useState('');
  const [modalType, setModalType] = useState('NAME');
  const [modalRole, setModalRole] = useState('');

  // AIRead Smart Sub-Term Replacement States
  const [fromTerm, setFromTerm] = useState('');
  const [toTerm, setToTerm] = useState('');
  const [fullTerm, setFullTerm] = useState('');
  const [notice, setNotice] = useState(null);

  useEffect(() => {
    if (notice) {
      const timer = setTimeout(() => setNotice(null), 8000);
      return () => clearTimeout(timer);
    }
  }, [notice]);

  useEffect(() => {
    loadProjects();
  }, []);

  useEffect(() => {
    if (selectedProjectId) loadEntities(selectedProjectId);
  }, [selectedProjectId]);

  const loadProjects = async () => {
    try {
      const res = await fetch('/api/v1/projects/?page=1&page_size=50');
      if (res.ok) {
        const data = await res.json();
        const items = data.items || [];
        setProjects(items);
        if (items.length > 0 && !selectedProjectId) {
          setSelectedProjectId(items[0].id);
        }
      }
    } catch (e) {
      console.error(e);
    }
  };

  const loadEntities = async (projId) => {
    setIsLoading(true);
    try {
      const res = await fetch(`/api/v1/projects/${projId}/entities`);
      if (res.ok) {
        const data = await res.json();
        const ent = data.entities || {};
        const list = ent.entities || [];
        setEntities(list);
        setSummary(ent.summary || '');

        const origMap = {};
        list.forEach(item => {
          if (item.chinese_name) {
            origMap[item.chinese_name] = item.vietnamese_name;
          }
        });
        setOriginalEntitiesMap(origMap);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setIsLoading(false);
    }
  };

  // Open Add Modal
  const handleOpenAddModal = () => {
    setEditingIndex(null);
    setModalChName('');
    setModalViName('');
    setFullTerm('');
    setFromTerm('');
    setToTerm('');
    setModalType('NAME');
    setModalRole('');
    setIsModalOpen(true);
  };

  // Open Edit Modal
  const handleOpenEditModal = (item, index) => {
    const globalIdx = entities.findIndex(e => e.chinese_name === item.chinese_name && e.vietnamese_name === item.vietnamese_name);
    setEditingIndex(globalIdx !== -1 ? globalIdx : index);
    setModalChName(item.chinese_name || '');
    setModalViName(item.vietnamese_name || '');
    setFullTerm(item.vietnamese_name || '');
    setFromTerm(item.vietnamese_name || '');
    setToTerm('');
    setModalType(item.entity_type || 'NAME');
    setModalRole(item.role || '');
    setIsModalOpen(true);
  };

  // Helper sync fromTerm & toTerm to fullTerm
  const handleFromOrToChange = (newFrom, newTo, origName) => {
    const orig = origName !== undefined ? origName : (editingIndex !== null ? entities[editingIndex]?.vietnamese_name : '') || '';
    if (newFrom.trim() && newTo.trim()) {
      try {
        const regex = new RegExp(newFrom.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i');
        if (regex.test(orig)) {
          setFullTerm(orig.replace(regex, newTo.trim()));
          return;
        }
      } catch (e) {}
    }
    if (newTo.trim() && !newFrom.trim()) {
      setFullTerm(newTo.trim());
    }
  };

  // Save Modal (Add or Edit)
  const handleSaveModal = async () => {
    const chName = modalChName.trim();
    const finalViName = (editingIndex !== null ? (fullTerm.trim() || modalViName.trim()) : modalViName.trim());

    if (!chName || !finalViName) {
      alert('Vui lòng nhập đầy đủ Ký tự Hán và Bản dịch Tiếng Việt!');
      return;
    }

    let updatedList = [...entities];
    const newItem = {
      chinese_name: chName,
      vietnamese_name: finalViName,
      entity_type: modalType,
      role: modalRole.trim() || '-'
    };

    if (editingIndex !== null && editingIndex >= 0 && editingIndex < updatedList.length) {
      updatedList[editingIndex] = newItem;
    } else {
      updatedList = [newItem, ...updatedList];
    }

    setEntities(updatedList);
    setIsModalOpen(false);

    // Lưu ngay lên backend
    if (selectedProjectId) {
      try {
        const payload = {
          summary,
          entities: updatedList,
          characters: updatedList.filter(e => e.entity_type === 'NAME').map(e => ({ raw: e.chinese_name, viet: e.vietnamese_name, role: e.role })),
          terms: updatedList.filter(e => e.entity_type !== 'NAME').map(e => ({ raw: e.chinese_name, viet: e.vietnamese_name, type: e.entity_type, category: e.role }))
        };
        await fetch(`/api/v1/projects/${selectedProjectId}/entities`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });

        // Tự động quét và áp dụng đổi cụm từ con (fromTerm -> toTerm) vào toàn bộ kịch bản
        const fTerm = fromTerm.trim();
        const tTerm = toTerm.trim();
        if (editingIndex !== null && fTerm && tTerm && fTerm.toLowerCase() !== tTerm.toLowerCase()) {
          const applyRes = await fetch(`/api/v1/projects/${selectedProjectId}/entities/apply`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              from_term: fTerm,
              to_term: tTerm
            })
          });
          if (applyRes.ok) {
            const data = await applyRes.json();
            setNotice({
              type: 'success',
              message: `Đã thay thế "${fTerm}" ➔ "${tTerm}": ${data.message}`
            });
            await loadEntities(selectedProjectId);
            return;
          }
        }

        setNotice({
          type: 'success',
          message: editingIndex !== null ? 'Đã cập nhật thực thể thành công!' : 'Đã thêm thực thể mới thành công!'
        });
      } catch (e) {
        console.error("Lỗi tự động lưu:", e);
        setNotice({ type: 'error', message: `Lỗi lưu thực thể: ${e.message}` });
      }
    }
  };

  // Delete entity
  const handleDeleteEntity = async (item) => {
    if (!window.confirm(`Bạn có chắc muốn xóa thực thể "${item.chinese_name} ➔ ${item.vietnamese_name}"?`)) return;

    const updatedList = entities.filter(e => !(e.chinese_name === item.chinese_name && e.vietnamese_name === item.vietnamese_name));
    setEntities(updatedList);

    if (selectedProjectId) {
      try {
        const payload = {
          summary,
          entities: updatedList,
          characters: updatedList.filter(e => e.entity_type === 'NAME').map(e => ({ raw: e.chinese_name, viet: e.vietnamese_name, role: e.role })),
          terms: updatedList.filter(e => e.entity_type !== 'NAME').map(e => ({ raw: e.chinese_name, viet: e.vietnamese_name, type: e.entity_type, category: e.role }))
        };
        await fetch(`/api/v1/projects/${selectedProjectId}/entities`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
      } catch (e) {
        console.error("Lỗi khi xóa:", e);
      }
    }
  };

  // Logic Sửa Từ Điển: Áp Dụng Thay Từ Vào Tất Cả Chương (AIREAD Style)
  const handleApplyToAllChapters = async () => {
    if (!selectedProjectId) return;

    const replacements = [];
    entities.forEach(item => {
      const origVi = originalEntitiesMap[item.chinese_name];
      if (origVi && origVi !== item.vietnamese_name) {
        replacements.push({
          old_val: origVi,
          new_val: item.vietnamese_name
        });
      }
    });

    if (replacements.length === 0) {
      const confirmAll = window.confirm('Bạn có muốn đồng bộ và áp dụng toàn bộ từ điển này vào tất cả các câu thoại trong kịch bản?');
      if (!confirmAll) return;
      entities.forEach(item => {
        replacements.push({
          old_val: item.chinese_name,
          new_val: item.vietnamese_name
        });
      });
    }

    setIsApplying(true);
    try {
      // 1. Lưu từ điển trước
      const payload = {
        summary,
        entities,
        characters: entities.filter(e => e.entity_type === 'NAME').map(e => ({ raw: e.chinese_name, viet: e.vietnamese_name, role: e.role })),
        terms: entities.filter(e => e.entity_type !== 'NAME').map(e => ({ raw: e.chinese_name, viet: e.vietnamese_name, type: e.entity_type, category: e.role }))
      };
      await fetch(`/api/v1/projects/${selectedProjectId}/entities`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      // 2. Gửi lệnh thay thế
      const res = await fetch(`/api/v1/projects/${selectedProjectId}/entities/apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ replacements })
      });

      if (!res.ok) throw new Error('Không thể áp dụng từ điển vào kịch bản');
      const data = await res.json();
      alert(`✔ ${data.message}`);

      const newOrig = {};
      entities.forEach(item => {
        newOrig[item.chinese_name] = item.vietnamese_name;
      });
      setOriginalEntitiesMap(newOrig);
    } catch (e) {
      alert(`Lỗi: ${e.message}`);
    } finally {
      setIsApplying(false);
    }
  };

  // Filtered List
  const filteredEntities = useMemo(() => {
    return entities.filter(item => {
      if (filterCategory !== 'ALL' && item.entity_type !== filterCategory) return false;
      if (search.trim()) {
        const s = search.toLowerCase();
        const ch = (item.chinese_name || '').toLowerCase();
        const vi = (item.vietnamese_name || '').toLowerCase();
        const role = (item.role || '').toLowerCase();
        const type = (item.entity_type || '').toLowerCase();
        return ch.includes(s) || vi.includes(s) || role.includes(s) || type.includes(s);
      }
      return true;
    });
  }, [entities, filterCategory, search]);

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      width: '100%',
      maxWidth: '1480px',
      margin: '0 auto',
      padding: '16px 20px',
      color: 'var(--text-main)',
      fontFamily: 'var(--font-main)',
      boxSizing: 'border-box'
    }}>
      
      {/* REAL-TIME NOTICE BANNER */}
      {notice && (
        <div style={{
          padding: '12px 18px',
          background: notice.type === 'error' ? '#fef2f2' : '#ecfdf5',
          border: notice.type === 'error' ? '1.5px solid #fecaca' : '1.5px solid #a7f3d0',
          borderRadius: '8px',
          color: notice.type === 'error' ? '#991b1b' : '#065f46',
          marginBottom: '16px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          fontSize: '13.5px',
          fontWeight: 600,
          boxShadow: '0 2px 8px rgba(0, 0, 0, 0.04)'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span>{notice.type === 'error' ? '⚠️' : '✅'}</span>
            <span>{notice.message}</span>
          </div>
          <button
            onClick={() => setNotice(null)}
            style={{
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              color: 'inherit',
              display: 'flex',
              alignItems: 'center',
              padding: '2px'
            }}
          >
            <X size={16} />
          </button>
        </div>
      )}

      {/* 1. AIREAD GLOSSARY HEADER CARD (CLEAN WHITE THEME) */}
      <div style={{
        background: '#ffffff',
        border: '1.5px solid #cbd5e1',
        borderRadius: '10px',
        padding: '18px 22px',
        display: 'flex',
        flexDirection: 'column',
        gap: '16px',
        marginBottom: '18px',
        boxShadow: '0 4px 12px rgba(15, 23, 42, 0.05)'
      }}>
        {/* Title Row */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
          <div style={{
            width: '42px',
            height: '42px',
            borderRadius: '10px',
            background: '#eff6ff',
            border: '1px solid #bfdbfe',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#2563eb'
          }}>
            <BookOpen size={22} />
          </div>

          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <h2 style={{ fontSize: '18px', fontWeight: 800, color: '#0f172a', letterSpacing: '0.2px' }}>
                Từ Điển & Quản Lý Thực Thể Truyện
              </h2>
              <span style={{
                background: '#eff6ff',
                color: '#2563eb',
                border: '1px solid #bfdbfe',
                padding: '2px 9px',
                borderRadius: '20px',
                fontSize: '11.5px',
                fontWeight: 700
              }}>
                {entities.length} thực thể
              </span>
            </div>
            <p style={{ color: '#64748b', fontSize: '13px', marginTop: '3px' }}>
              Khóa tên riêng nhân vật, bảo vật, địa danh và tự động thay thế chuẩn xác trong toàn bộ chương
            </p>
          </div>
        </div>

        {/* Controls Row (Truyện Select, Phạm vi, Nút Áp Dụng) */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '14px', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span style={{ fontSize: '13px', color: '#334155', fontWeight: 700 }}>Truyện:</span>
            <select
              value={selectedProjectId || ''}
              onChange={(e) => setSelectedProjectId(parseInt(e.target.value, 10))}
              style={{
                padding: '8px 14px',
                background: '#ffffff',
                border: '1px solid #cbd5e1',
                borderRadius: '8px',
                color: '#0f172a',
                fontSize: '13px',
                fontWeight: 600,
                minWidth: '280px',
                outline: 'none'
              }}
            >
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.title}
                </option>
              ))}
            </select>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span style={{ fontSize: '13px', color: '#334155', fontWeight: 700 }}>Phạm vi:</span>
            <select
              value={scope}
              onChange={(e) => setScope(e.target.value)}
              style={{
                padding: '8px 14px',
                background: '#ffffff',
                border: '1px solid #cbd5e1',
                borderRadius: '8px',
                color: '#0f172a',
                fontSize: '13px',
                outline: 'none'
              }}
            >
              <option value="ALL">📖 Tất Cả Chương</option>
              <option value="1">Chương 1 (Lô 1)</option>
              <option value="2">Chương 2 (Lô 2)</option>
            </select>
          </div>

          <button
            onClick={handleApplyToAllChapters}
            disabled={isApplying}
            className="btn btn-emerald"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '8px',
              padding: '8px 18px',
              fontSize: '13px',
              fontWeight: 700
            }}
          >
            {isApplying ? <RefreshCw size={14} className="animate-spin" /> : <Sparkles size={14} />}
            Áp Dụng Thay Từ Vào Tất Cả Chương
          </button>
        </div>
      </div>

      {/* 2. SEARCH & FILTER & ADD ACTION BAR */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '16px', marginBottom: '18px', flexWrap: 'wrap' }}>
        
        {/* Left: Search Input */}
        <div style={{ position: 'relative', flex: 1, maxWidth: '520px' }}>
          <Search size={16} color="#2563eb" style={{ position: 'absolute', left: '14px', top: '50%', transform: 'translateY(-50%)' }} />
          <input
            type="text"
            placeholder="Tìm kiếm thực thể... (tên Hán, tên Việt, phân loại)"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{
              width: '100%',
              padding: '9px 14px 9px 40px',
              background: '#ffffff',
              border: '1px solid #cbd5e1',
              borderRadius: '8px',
              color: '#0f172a',
              fontSize: '13px',
              outline: 'none'
            }}
          />
        </div>

        {/* Middle & Right: Category Filter Dropdown & Add Button */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <select
            value={filterCategory}
            onChange={(e) => setFilterCategory(e.target.value)}
            style={{
              padding: '9px 16px',
              background: '#ffffff',
              border: '1px solid #cbd5e1',
              borderRadius: '8px',
              color: '#0f172a',
              fontSize: '13px',
              fontWeight: 600,
              outline: 'none',
              cursor: 'pointer'
            }}
          >
            <option value="ALL">🗂️ Tất Cả Phân Loại ({entities.length})</option>
            <option value="NAME">👤 Nhân Vật ({entities.filter(e => e.entity_type === 'NAME').length})</option>
            <option value="SECT">🏰 Môn Phái ({entities.filter(e => e.entity_type === 'SECT').length})</option>
            <option value="PLACE">🗺️ Địa Danh ({entities.filter(e => e.entity_type === 'PLACE').length})</option>
            <option value="ITEM">⚔️ Pháp Bảo ({entities.filter(e => e.entity_type === 'ITEM').length})</option>
            <option value="SKILL">📜 Võ Học ({entities.filter(e => e.entity_type === 'SKILL').length})</option>
            <option value="CREATURE">🐉 Linh Thú ({entities.filter(e => e.entity_type === 'CREATURE').length})</option>
            <option value="OTHER">⚡ Khác ({entities.filter(e => e.entity_type === 'OTHER').length})</option>
          </select>

          <button
            onClick={handleOpenAddModal}
            className="btn btn-primary"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '8px',
              padding: '9px 18px',
              fontSize: '13px',
              fontWeight: 800
            }}
          >
            <Plus size={16} /> + Thêm Thực Thể Mới
          </button>
        </div>
      </div>

      {/* 3. SUBHEADER: COUNT */}
      <div style={{ fontSize: '14px', fontWeight: 800, color: '#0f172a', marginBottom: '14px' }}>
        Danh Sách Thực Thể ({filteredEntities.length})
      </div>

      {/* 4. ENTITY CARDS GRID: 3 COLUMNS (CLEAN WHITE THEME) */}
      {isLoading ? (
        <div style={{ textAlign: 'center', padding: '80px', color: '#64748b' }}>
          ⏳ Đang nạp danh sách thực thể...
        </div>
      ) : filteredEntities.length === 0 ? (
        <div style={{
          textAlign: 'center',
          padding: '80px',
          color: '#64748b',
          background: '#ffffff',
          borderRadius: '10px',
          border: '1.5px solid #cbd5e1',
          boxShadow: '0 4px 12px rgba(15, 23, 42, 0.05)'
        }}>
          Không tìm thấy thực thể nào. Nhấn <strong>[+ Thêm Thực Thể Mới]</strong> để thêm!
        </div>
      ) : (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(350px, 1fr))',
          gap: '14px',
          paddingBottom: '40px'
        }}>
          {filteredEntities.map((item, index) => {
            const isModified = originalEntitiesMap[item.chinese_name] && originalEntitiesMap[item.chinese_name] !== item.vietnamese_name;

            return (
              <div
                key={index}
                style={{
                  background: '#ffffff',
                  border: isModified ? '1.5px solid #f59e0b' : '1.5px solid #cbd5e1',
                  borderRadius: '10px',
                  padding: '12px 16px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: '12px',
                  boxShadow: '0 4px 12px rgba(15, 23, 42, 0.05)',
                  transition: 'border-color 0.2s ease, box-shadow 0.2s ease'
                }}
              >
                {/* Left: Chinese Name Badge (Golden/Yellow Amber on Light) */}
                <div style={{
                  padding: '6px 12px',
                  background: '#fef3c7',
                  border: '1px solid #fde68a',
                  borderRadius: '6px',
                  color: '#b45309',
                  fontSize: '14px',
                  fontWeight: 800,
                  whiteSpace: 'nowrap',
                  flexShrink: 0
                }}>
                  {item.chinese_name}
                </div>

                {/* Arrow */}
                <span style={{ color: '#94a3b8', fontSize: '13px', flexShrink: 0 }}>➔</span>

                {/* Middle: Vietnamese Name & Category Badge */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{
                    fontSize: '14.5px',
                    fontWeight: 700,
                    color: '#0f172a',
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis'
                  }}>
                    {item.vietnamese_name}
                  </div>
                  <div style={{ marginTop: '3px' }}>
                    <span style={{
                      display: 'inline-block',
                      padding: '1px 7px',
                      background: '#f1f5f9',
                      border: '1px solid #e2e8f0',
                      borderRadius: '4px',
                      fontSize: '10.5px',
                      fontFamily: 'var(--font-mono)',
                      color: '#475569',
                      fontWeight: 700
                    }}>
                      {item.entity_type || 'NAME'}
                    </span>
                  </div>
                </div>

                {/* Right: Actions (Edit & Delete) */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexShrink: 0 }}>
                  <button
                    onClick={() => handleOpenEditModal(item, index)}
                    title="Chỉnh sửa thực thể"
                    style={{
                      background: 'transparent',
                      border: 'none',
                      color: '#64748b',
                      cursor: 'pointer',
                      padding: '5px',
                      borderRadius: '4px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center'
                    }}
                    onMouseEnter={(e) => e.currentTarget.style.color = '#2563eb'}
                    onMouseLeave={(e) => e.currentTarget.style.color = '#64748b'}
                  >
                    <Edit3 size={15} />
                  </button>

                  <button
                    onClick={() => handleDeleteEntity(item)}
                    title="Xóa thực thể"
                    style={{
                      background: 'transparent',
                      border: 'none',
                      color: '#64748b',
                      cursor: 'pointer',
                      padding: '5px',
                      borderRadius: '4px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center'
                    }}
                    onMouseEnter={(e) => e.currentTarget.style.color = '#e11d48'}
                    onMouseLeave={(e) => e.currentTarget.style.color = '#64748b'}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* 5. MODAL: THÊM / SỬA THỰC THỂ (AIREAD SMART MODAL STYLE) */}
      {isModalOpen && (
        <div style={{
          position: 'fixed',
          top: 0,
          left: 0,
          width: '100vw',
          height: '100vh',
          background: 'rgba(15, 23, 42, 0.5)',
          backdropFilter: 'blur(6px)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 1000
        }}>
          <div style={{
            width: editingIndex !== null ? '540px' : '460px',
            background: '#ffffff',
            border: '1.5px solid #cbd5e1',
            borderRadius: '10px',
            padding: '24px',
            boxShadow: '0 20px 25px -5px rgba(0, 0, 0, 0.15)',
            display: 'flex',
            flexDirection: 'column',
            gap: '16px'
          }}>
            {/* Modal Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--border)', paddingBottom: '12px' }}>
              <h3 style={{ fontSize: '16px', fontWeight: 800, color: '#0f172a' }}>
                {editingIndex !== null ? '✏️ Chỉnh Sửa Thực Thể & Thay Cụm Từ' : '➕ Thêm Thực Thể Mới'}
              </h3>
              <button
                onClick={() => setIsModalOpen(false)}
                style={{ background: 'transparent', border: 'none', color: '#64748b', cursor: 'pointer' }}
              >
                <X size={18} />
              </button>
            </div>

            {/* Modal Form */}
            {editingIndex !== null ? (
              /* SMART MODAL EDIT MODE (AIREAD 3-FIELD STYLE) */
              <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                {/* 1. Ký tự Hán gốc Badge */}
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '10px',
                  padding: '10px 14px',
                  background: '#fef3c7',
                  border: '1px solid #fde68a',
                  borderRadius: '8px'
                }}>
                  <span style={{ fontSize: '12px', fontWeight: 700, color: '#92400e' }}>Ký Tự Hán Gốc:</span>
                  <span style={{ fontSize: '16px', fontWeight: 800, color: '#b45309' }}>{modalChName}</span>
                  <span style={{
                    marginLeft: 'auto',
                    fontSize: '11px',
                    color: '#475569',
                    background: '#ffffff',
                    border: '1px solid #cbd5e1',
                    padding: '2px 8px',
                    borderRadius: '4px',
                    fontWeight: 700
                  }}>
                    {CATEGORY_MAP[modalType] || modalType}
                  </span>
                </div>

                {/* 2. AIRead Smart Sub-term Box (Từ con đổi ➔ Đổi thành) */}
                <div style={{
                  background: '#f8fafc',
                  border: '1.5px dashed #cbd5e1',
                  borderRadius: '8px',
                  padding: '12px 14px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '10px'
                }}>
                  <div style={{ fontSize: '12.5px', fontWeight: 700, color: '#0f172a', display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <span>🎯</span>
                    <span>Sửa Cụm Từ Con (Tự động quét & đổi trong kịch bản)</span>
                  </div>

                  <div style={{ display: 'grid', gridTemplateColumns: '1fr auto 1fr', gap: '10px', alignItems: 'center' }}>
                    <div>
                      <label style={{ fontSize: '11px', color: '#64748b', fontWeight: 700, display: 'block', marginBottom: '3px' }}>
                        Từ con cần đổi:
                      </label>
                      <input
                        type="text"
                        placeholder="VD: bưu đầu"
                        value={fromTerm}
                        onChange={(e) => {
                          const val = e.target.value;
                          setFromTerm(val);
                          handleFromOrToChange(val, toTerm);
                        }}
                        style={{
                          width: '100%',
                          padding: '8px 10px',
                          background: '#ffffff',
                          border: '1px solid #cbd5e1',
                          borderRadius: '6px',
                          fontSize: '13px',
                          fontWeight: 700,
                          color: '#dc2626',
                          outline: 'none'
                        }}
                      />
                    </div>

                    <div style={{ paddingTop: '16px', color: '#94a3b8', display: 'flex', justifyContent: 'center' }}>
                      <ArrowRight size={18} />
                    </div>

                    <div>
                      <label style={{ fontSize: '11px', color: '#64748b', fontWeight: 700, display: 'block', marginBottom: '3px' }}>
                        Đổi thành:
                      </label>
                      <input
                        type="text"
                        placeholder="VD: tiêu đầu"
                        value={toTerm}
                        onChange={(e) => {
                          const val = e.target.value;
                          setToTerm(val);
                          handleFromOrToChange(fromTerm, val);
                        }}
                        style={{
                          width: '100%',
                          padding: '8px 10px',
                          background: '#ffffff',
                          border: '1px solid #cbd5e1',
                          borderRadius: '6px',
                          fontSize: '13px',
                          fontWeight: 700,
                          color: '#16a34a',
                          outline: 'none'
                        }}
                      />
                    </div>
                  </div>
                  <p style={{ fontSize: '11px', color: '#64748b', margin: 0, lineHeight: 1.4 }}>
                    💡 Khi gõ cụm từ sai (ví dụ <i>bưu đầu</i>) sang đúng (<i>tiêu đầu</i>), hệ thống sẽ cập nhật tên thực thể bên dưới và quét đổi toàn bộ câu thoại trong phim!
                  </p>
                </div>

                {/* 3. Tên thực thể đầy đủ */}
                <div>
                  <label style={{ fontSize: '12px', color: '#334155', fontWeight: 700, display: 'block', marginBottom: '4px' }}>
                    Tên Thực Thể Đầy Đủ (Lưu CSDL & Từ Điển):
                  </label>
                  <input
                    type="text"
                    placeholder="VD: Trần Tiêu Đầu"
                    value={fullTerm}
                    onChange={(e) => setFullTerm(e.target.value)}
                    style={{
                      width: '100%',
                      padding: '9px 12px',
                      background: '#ffffff',
                      border: '1.5px solid #2563eb',
                      borderRadius: '6px',
                      color: '#0f172a',
                      fontWeight: 700,
                      fontSize: '14px',
                      outline: 'none'
                    }}
                  />
                </div>

                {/* 4. Phân loại & Ghi chú */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                  <div>
                    <label style={{ fontSize: '12px', color: '#334155', fontWeight: 700, display: 'block', marginBottom: '4px' }}>
                      Phân Loại:
                    </label>
                    <select
                      value={modalType}
                      onChange={(e) => setModalType(e.target.value)}
                      style={{
                        width: '100%',
                        padding: '8px 10px',
                        background: '#ffffff',
                        border: '1px solid #cbd5e1',
                        borderRadius: '6px',
                        color: '#0f172a',
                        fontSize: '12.5px',
                        outline: 'none'
                      }}
                    >
                      <option value="NAME">👤 Nhân Vật (NAME)</option>
                      <option value="SECT">🏰 Môn Phái (SECT)</option>
                      <option value="PLACE">🗺️ Địa Danh (PLACE)</option>
                      <option value="ITEM">⚔️ Pháp Bảo (ITEM)</option>
                      <option value="SKILL">📜 Võ Học (SKILL)</option>
                      <option value="CREATURE">🐉 Linh Thú (CREATURE)</option>
                      <option value="OTHER">⚡ Khác (OTHER)</option>
                    </select>
                  </div>

                  <div>
                    <label style={{ fontSize: '12px', color: '#334155', fontWeight: 700, display: 'block', marginBottom: '4px' }}>
                      Vai Trò / Ghi Chú:
                    </label>
                    <input
                      type="text"
                      placeholder="VD: Tiêu đầu..."
                      value={modalRole}
                      onChange={(e) => setModalRole(e.target.value)}
                      style={{
                        width: '100%',
                        padding: '8px 10px',
                        background: '#ffffff',
                        border: '1px solid #cbd5e1',
                        borderRadius: '6px',
                        color: '#0f172a',
                        fontSize: '12.5px',
                        outline: 'none'
                      }}
                    />
                  </div>
                </div>
              </div>
            ) : (
              /* ADD NEW ENTITY FORM */
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                <div>
                  <label style={{ fontSize: '12px', color: '#334155', fontWeight: 700, display: 'block', marginBottom: '4px' }}>
                    Ký Tự Hán Gốc:
                  </label>
                  <input
                    type="text"
                    placeholder="VD: 陆沉 / 青云宗 / 降龙掌"
                    value={modalChName}
                    onChange={(e) => setModalChName(e.target.value)}
                    style={{
                      width: '100%',
                      padding: '8px 12px',
                      background: '#fef3c7',
                      border: '1px solid #fde68a',
                      borderRadius: '6px',
                      color: '#b45309',
                      fontWeight: 700,
                      fontSize: '14px',
                      outline: 'none'
                    }}
                  />
                </div>

                <div>
                  <label style={{ fontSize: '12px', color: '#334155', fontWeight: 700, display: 'block', marginBottom: '4px' }}>
                    Bản Dịch Tiếng Việt:
                  </label>
                  <input
                    type="text"
                    placeholder="VD: Lục Trầm / Thanh Vân Tông / Hàng Long Chưởng"
                    value={modalViName}
                    onChange={(e) => setModalViName(e.target.value)}
                    style={{
                      width: '100%',
                      padding: '8px 12px',
                      background: '#ffffff',
                      border: '1px solid #cbd5e1',
                      borderRadius: '6px',
                      color: '#0f172a',
                      fontWeight: 700,
                      fontSize: '14px',
                      outline: 'none'
                    }}
                  />
                </div>

                <div>
                  <label style={{ fontSize: '12px', color: '#334155', fontWeight: 700, display: 'block', marginBottom: '4px' }}>
                    Phân Loại Thực Thể:
                  </label>
                  <select
                    value={modalType}
                    onChange={(e) => setModalType(e.target.value)}
                    style={{
                      width: '100%',
                      padding: '8px 12px',
                      background: '#ffffff',
                      border: '1px solid #cbd5e1',
                      borderRadius: '6px',
                      color: '#0f172a',
                      fontSize: '13px',
                      outline: 'none'
                    }}
                  >
                    <option value="NAME">👤 Nhân Vật (NAME)</option>
                    <option value="SECT">🏰 Môn Phái & Thế Lực (SECT)</option>
                    <option value="PLACE">🗺️ Địa Danh & Không Gian (PLACE)</option>
                    <option value="ITEM">⚔️ Pháp Bảo & Đan Dược (ITEM)</option>
                    <option value="SKILL">📜 Công Pháp & Võ Học (SKILL)</option>
                    <option value="CREATURE">🐉 Linh Thú & Thần Thú (CREATURE)</option>
                    <option value="OTHER">⚡ Cảnh Giới & Thuật Ngữ (OTHER)</option>
                  </select>
                </div>

                <div>
                  <label style={{ fontSize: '12px', color: '#334155', fontWeight: 700, display: 'block', marginBottom: '4px' }}>
                    Vai Trò / Ghi Chú:
                  </label>
                  <input
                    type="text"
                    placeholder="VD: Nhân vật chính, đệ tử tông môn..."
                    value={modalRole}
                    onChange={(e) => setModalRole(e.target.value)}
                    style={{
                      width: '100%',
                      padding: '8px 12px',
                      background: '#ffffff',
                      border: '1px solid #cbd5e1',
                      borderRadius: '6px',
                      color: '#0f172a',
                      fontSize: '13px',
                      outline: 'none'
                    }}
                  />
                </div>
              </div>
            )}

            {/* Modal Actions */}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '8px', borderTop: '1px solid var(--border)', paddingTop: '14px' }}>
              <button
                onClick={() => setIsModalOpen(false)}
                className="btn btn-secondary btn-sm"
                style={{ padding: '8px 16px', fontSize: '13px' }}
              >
                Hủy
              </button>
              <button
                onClick={handleSaveModal}
                className="btn btn-primary"
                style={{ padding: '8px 20px', fontSize: '13px', fontWeight: 800 }}
              >
                {editingIndex !== null ? 'Lưu & Áp Dụng Thay Từ' : '+ Thêm Thực Thể'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
