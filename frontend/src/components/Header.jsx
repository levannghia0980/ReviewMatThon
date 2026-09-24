import React from 'react';
import { RefreshCw, Video, MessageSquare } from 'lucide-react';

export default function Header({ activeTab, totalVideos, totalDialogues, onRefresh }) {
  const titles = {
    auto: {
      title: '⚡ Lồng Tiếng Tự Động 1-Click (100% Automated)',
      sub: 'Dán URL ➔ Whisper STT ➔ Dịch AIREAD ➔ Quét sạch Hán ➔ TikTok TTS ➔ Xuất Video Karaoke & Ducking'
    },
    projects: {
      title: '📂 Danh Sách Dự Án & Quản Lý Video',
      sub: 'Quản lý toàn bộ video trong Database SQLite, mở trong Studio hoặc xem kịch bản'
    },
    translation: {
      title: '✍️ Biên Tập Lời Thoại & Bóc Tách Thực Thể AIREAD',
      sub: 'Dịch thuật đa tầng theo lô token tối đa, bóc tách nhân vật, sửa lỗi chữ Hán tự động'
    },
    voiceover: {
      title: '🎙️ Lồng Tiếng TikTok TTS (Smart Headroom Alignment)',
      sub: 'Khớp chuẩn 100% mốc thời gian, xử lý nén nhịp thở tự nhiên, không bao giờ lệch dồn'
    },
    composer: {
      title: '🎬 Xuất Bản Video Review Hoàn Chỉnh (Video Composer Studio)',
      sub: 'Phủ hộp nền che phụ đề cũ, đè phụ đề Karaoke chữ sáng từng từ, Logo góc & Watermark chống clone'
    },
    settings: {
      title: '⚙️ Cài Đặt API Keys & Trạng Thái Hệ Thống',
      sub: 'Quản lý Groq API, Gemini API, OpenRouter, TikTok TTS Session ID và cấu hình phần cứng'
    }
  };

  const current = titles[activeTab] || titles.auto;


  return (
    <header className="top-header">
      <div className="header-title-box">
        <h1>{current.title}</h1>
        <p>{current.sub}</p>
      </div>

      <div className="header-stats">
        <div className="stat-pill">
          <Video size={16} color="var(--cyan)" />
          <span>Tổng Video:</span>
          <strong>{totalVideos}</strong>
        </div>

        <div className="stat-pill violet">
          <MessageSquare size={16} color="var(--purple)" />
          <span>Tổng Lời Thoại:</span>
          <strong>{totalDialogues}</strong>
        </div>

        <button className="btn btn-secondary btn-sm" onClick={onRefresh} title="Làm mới dữ liệu">
          <RefreshCw size={14} />
        </button>
      </div>
    </header>
  );
}
