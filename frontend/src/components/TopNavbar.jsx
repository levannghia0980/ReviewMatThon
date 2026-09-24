import React from 'react';
import { Film, BookOpen, Layers, HardDrive, Settings, Activity, RefreshCw } from 'lucide-react';

export default function TopNavbar({ activeTab, setActiveTab, totalVideos, totalDialogues, onRefresh }) {
  const navItems = [
    { id: 'studio', label: '🎬 Xưởng Video (Chính)', desc: 'Player to, che chữ & 1-click' },
    { id: 'dialogues', label: '📖 Thư Viện Thoại', desc: 'Quản lý kịch bản AIREAD' },
    { id: 'entities', label: '📚 Từ Điển Thực Thể', desc: 'Nhân vật & xưng hô AIREAD' },
    { id: 'gallery', label: '📂 Kho Video Tổng Thể', desc: 'Phân loại & xem video' },
    { id: 'settings', label: '⚙️ Cài Đặt API', desc: 'Groq, Gemini, TikTok' }
  ];

  return (
    <header className="top-navbar-container">
      {/* Brand Logo */}
      <div className="brand-wrapper">
        <div className="brand-icon-box">
          🔥
        </div>
        <div>
          <div className="brand-title">
            REVIEW MẮT THẦN
          </div>
          <div className="brand-subtitle">
            AI Video Dubbing Studio 4.0
          </div>
        </div>
      </div>

      {/* Horizontal Nav Tabs */}
      <nav style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
        {navItems.map((item) => {
          const isActive = activeTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => setActiveTab(item.id)}
              className={`nav-tab-btn ${isActive ? 'active' : ''}`}
            >
              <span>{item.label}</span>
            </button>
          );
        })}
      </nav>

      {/* Right Stats & Status */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          padding: '5px 12px',
          background: '#ecfdf5',
          border: '1px solid #a7f3d0',
          borderRadius: '20px',
          fontSize: '12px',
          color: '#047857',
          fontWeight: 700
        }}>
          <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: '#059669' }} />
          Online ({totalVideos} video)
        </div>

        <button
          onClick={onRefresh}
          title="Làm mới dữ liệu"
          className="btn btn-secondary btn-sm"
          style={{ padding: '7px 12px' }}
        >
          <RefreshCw size={14} /> Làm Mới
        </button>
      </div>
    </header>
  );
}
