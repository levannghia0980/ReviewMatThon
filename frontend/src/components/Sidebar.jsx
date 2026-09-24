import React from 'react';
import { Zap, Film, FileText, Mic, Sliders, Settings } from 'lucide-react';

export default function Sidebar({ activeTab, setActiveTab, totalProjects }) {
  const navItems = [
    { id: 'auto', label: '⚡ Tự Động 1-Click', icon: <Zap size={18} /> },
    { id: 'projects', label: '📂 Video & Dự Án', icon: <Film size={18} />, badge: totalProjects },
    { id: 'translation', label: '✍️ Bản Dịch & Thực Thể', icon: <FileText size={18} /> },
    { id: 'voiceover', label: '🎙️ Lồng Tiếng TikTok', icon: <Mic size={18} /> },
    { id: 'composer', label: '🎬 Xuất Video Studio', icon: <Sliders size={18} /> },
    { id: 'settings', label: '⚙️ Cài Đặt & API Key', icon: <Settings size={18} /> }
  ];



  return (
    <aside className="sidebar">
      {/* Brand Header */}
      <div className="brand-wrapper">
        <div className="brand-icon-box">⚡</div>
        <div>
          <h2 className="brand-title">MẮT THẦN AI</h2>
          <span className="brand-subtitle">Studio Thuyết Minh 4.0</span>
        </div>
      </div>

      {/* Navigation */}
      <nav className="nav-list">
        {navItems.map((item) => (
          <button
            key={item.id}
            className={`nav-btn ${activeTab === item.id ? 'active' : ''}`}
            onClick={() => setActiveTab(item.id)}
          >
            <span className="nav-btn-icon">{item.icon}</span>
            <span>{item.label}</span>
            {item.badge !== undefined && (
              <span className="nav-badge">{item.badge}</span>
            )}
          </button>
        ))}
      </nav>

      {/* Footer System Status */}
      <div className="sidebar-footer">
        <div className="system-status-card">
          <div className="pulse-dot"></div>
          <div>
            <strong style={{ fontSize: '12px', display: 'block', color: 'var(--text-main)' }}>
              FastAPI + SQLite
            </strong>
            <small style={{ fontSize: '11px', color: 'var(--emerald)', fontWeight: 600 }}>
              ● Engine Online (60 FPS)
            </small>
          </div>
        </div>
      </div>
    </aside>
  );
}
