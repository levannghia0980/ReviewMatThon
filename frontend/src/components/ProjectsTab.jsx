import React, { useState, useEffect } from 'react';
import { Search, FileText, Trash2, RotateCcw, ChevronLeft, ChevronRight, Clock, Hash } from 'lucide-react';

export default function ProjectsTab({ onOpenModal, onSelectForStudio }) {

  const [projects, setProjects] = useState([]);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [totalPages, setTotalPages] = useState(1);
  const [totalItems, setTotalItems] = useState(0);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('ALL');
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    loadProjects();
  }, [page, pageSize, statusFilter]);

  // Debounced search
  useEffect(() => {
    const timer = setTimeout(() => {
      setPage(1);
      loadProjects();
    }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  const loadProjects = async () => {
    setIsLoading(true);
    try {
      const params = new URLSearchParams({
        page,
        page_size: pageSize,
      });
      if (statusFilter !== 'ALL') params.append('status', statusFilter);
      if (search) params.append('search', search);

      const res = await fetch(`/api/v1/projects/?${params.toString()}`);
      const data = await res.json();
      setProjects(data.items || []);
      setTotalPages(data.total_pages || 1);
      setTotalItems(data.total_items || 0);
    } catch (err) {
      console.error(err);
    } finally {
      setIsLoading(false);
    }
  };

  const handleReset = async (id, title) => {
    if (!window.confirm(`⚠️ BẠN CÓ CHẮC MUỐN RESET DỰ ÁN #${id} VỀ BAN ĐẦU?\n\n"${title || id}"\n\nThao tác này sẽ:\n✓ GIỮ NGUYÊN FILE VIDEO GỐC ĐÃ TẢI VỀ\n✗ Xóa toàn bộ câu thoại, bản dịch tiếng Việt\n✗ Xóa toàn bộ file thuyết minh audio và video render thành phẩm\n\nDự án sẽ trở về trạng thái như lúc mới tải xong!`)) return;

    try {
      const res = await fetch(`/api/v1/projects/${id}/reset`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Reset thất bại');
      alert(data.message || `Đã reset dự án #${id} về lúc mới tải xong thành công!`);
      loadProjects();
    } catch (err) {
      alert(`Lỗi: ${err.message}`);
    }
  };

  const handleDelete = async (id, title) => {
    if (!window.confirm(`⚠️ BẠN CÓ CHẮC MUỐN XÓA DỰ ÁN #${id}?\n\n"${title || id}"\n\nThao tác này sẽ xóa vĩnh viễn 100% video gốc, audio, sub dịch, file lồng tiếng và bản render cuối khỏi ổ đĩa!`)) return;

    try {
      const res = await fetch(`/api/v1/projects/${id}`, { method: 'DELETE' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Xóa thất bại');
      alert(data.message || `Đã xóa dự án #${id} thành công!`);
      loadProjects();
    } catch (err) {
      alert(`Lỗi: ${err.message}`);
    }
  };

  const formatDuration = (seconds) => {
    if (!seconds) return '00:00';
    const s = Math.floor(seconds);
    const hrs = Math.floor(s / 3600);
    const mins = Math.floor((s % 3600) / 60);
    const secs = s % 60;
    if (hrs > 0) {
      return `${hrs}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    }
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  };

  return (
    <div className="glass-panel">
      {/* Toolbar */}
      <div className="table-filter-bar">
        <div className="search-field">
          <input
            type="text"
            placeholder="🔍 Tìm kiếm theo Tiêu đề hoặc Video ID (BV...)..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        <div style={{ display: 'flex', gap: '12px' }}>
          <select value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}>
            <option value="ALL">Tất Cả Trạng Thái</option>
            <option value="TRANSCRIBED">Đã Bóc Lời Thoại</option>
            <option value="COMPLETED">Hoàn Thành</option>
            <option value="PENDING">Đang Chờ</option>
          </select>

          <select value={pageSize} onChange={(e) => { setPageSize(parseInt(e.target.value, 10)); setPage(1); }}>
            <option value={10}>10 video / trang</option>
            <option value={20}>20 video / trang</option>
            <option value={50}>50 video / trang</option>
          </select>
        </div>
      </div>

      {/* Table */}
      <div className="table-wrapper">
        <table className="modern-table">
          <thead>
            <tr>
              <th width="60">ID</th>
              <th>Tiêu Đề Video</th>
              <th width="120">Thời Lượng</th>
              <th width="120">Số Lời Thoại</th>
              <th width="140">Trạng Thái</th>
              <th width="160">Ngày Tạo</th>
              <th width="200" style={{ textAlign: 'right' }}>Hành Động</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan="7" style={{ textAlign: 'center', padding: '30px', color: 'var(--text-dim)' }}>
                  ⏳ Đang nạp dữ liệu siêu nhẹ từ SQLite...
                </td>
              </tr>
            ) : projects.length === 0 ? (
              <tr>
                <td colSpan="7" style={{ textAlign: 'center', padding: '30px', color: 'var(--text-dim)' }}>
                  Chưa có video nào. Hãy dán link tại Tab "Tải & Bóc Tách" để bắt đầu!
                </td>
              </tr>
            ) : (
              projects.map((p) => (
                <tr key={p.id}>
                  <td>
                    <strong style={{ color: 'var(--cyan)', fontFamily: 'var(--font-mono)' }}>
                      #{p.id}
                    </strong>
                  </td>
                  <td>
                    <div className="project-title-box">
                      <strong>{p.title}</strong>
                      <small>ID: {p.video_id}</small>
                    </div>
                  </td>
                  <td>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontFamily: 'var(--font-mono)' }}>
                      <Clock size={13} color="var(--amber)" />
                      <span>{formatDuration(p.duration)}</span>
                    </div>
                  </td>
                  <td>
                    <span className="tag-badge violet" style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
                      <Hash size={11} /> {p.total_dialogues} câu
                    </span>
                  </td>
                  <td>
                    <span className="tag-badge emerald">{p.status}</span>
                  </td>
                  <td style={{ color: 'var(--text-dim)', fontFamily: 'var(--font-mono)', fontSize: '12px' }}>
                    {p.created_at || 'Vừa tạo'}
                  </td>
                  <td style={{ textAlign: 'right' }}>
                    <div style={{ display: 'flex', gap: '6px', justifyContent: 'flex-end' }}>
                      <button
                        className="btn btn-primary btn-sm"
                        onClick={() => onSelectForStudio && onSelectForStudio(p.id)}
                        title="Mở trong Studio Biên Tập Dịch Thuật & Lồng Tiếng"
                        style={{ background: 'var(--grad-purple)', padding: '5px 10px', fontSize: '11px', fontWeight: 700 }}
                      >
                        ✨ Mở Studio
                      </button>
                      <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => onOpenModal(p.id, p.title)}
                        title="Xem toàn bộ kịch bản và mốc thời gian"
                      >
                        <FileText size={13} /> Kịch Bản
                      </button>
                      <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => handleReset(p.id, p.title)}
                        title="Reset về lúc mới tải xong (giữ lại video gốc, xóa câu thoại/audio/video render để làm lại)"
                        style={{ color: 'var(--amber)', borderColor: 'rgba(245, 158, 11, 0.4)' }}
                      >
                        <RotateCcw size={13} />
                      </button>
                      <button
                        className="btn btn-danger btn-sm"
                        onClick={() => handleDelete(p.id, p.title)}
                        title="Xóa vĩnh viễn project"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>

                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="table-pagination">
        <div style={{ fontSize: '12px', color: 'var(--text-dim)' }}>
          Hiển thị <strong>{totalItems === 0 ? 0 : (page - 1) * pageSize + 1}</strong> - <strong>{Math.min(page * pageSize, totalItems)}</strong> trong tổng số <strong>{totalItems}</strong> video
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <button
            className="btn btn-secondary btn-sm"
            disabled={page <= 1 || isLoading}
            onClick={() => setPage((prev) => Math.max(prev - 1, 1))}
          >
            <ChevronLeft size={14} /> Trang Trước
          </button>

          <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', fontWeight: 800, color: 'var(--cyan)' }}>
            Trang {page} / {totalPages}
          </span>

          <button
            className="btn btn-secondary btn-sm"
            disabled={page >= totalPages || isLoading}
            onClick={() => setPage((prev) => Math.min(prev + 1, totalPages))}
          >
            Trang Sau <ChevronRight size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}
