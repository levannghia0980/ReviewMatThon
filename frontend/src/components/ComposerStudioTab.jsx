import React, { useState, useEffect } from 'react';
import { 
  Film, Download, RefreshCw, CheckCircle2, Play, 
  Sliders, ShieldCheck, Music, Sparkles, Terminal, Eye
} from 'lucide-react';

export default function ComposerStudioTab({ initialProjectId }) {
  const [projects, setProjects] = useState([]);
  const [selectedProjectId, setSelectedProjectId] = useState(initialProjectId || null);
  const [projectInfo, setProjectInfo] = useState(null);

  // Video Composer Parameters (Mặc định Khung Trắng bo tròn chữ Đen che sạch chữ gốc)
  const [boxStyle, setBoxStyle] = useState('white_box');
  const [boxPadding, setBoxPadding] = useState(8);
  const [marginV, setMarginV] = useState(12);
  const [fontSize, setFontSize] = useState(13);
  const [backdropOpacity, setBackdropOpacity] = useState('99');
  const [karaokeColor, setKaraokeColor] = useState('&H000000FF');
  const [channelName, setChannelName] = useState('@Mắt Thần Review');
  const [channelOpacity, setChannelOpacity] = useState(0.35);
  const [logoPosition, setLogoPosition] = useState('top_left');
  const [logoSize, setLogoSize] = useState(120);

  // Render State
  const [isRendering, setIsRendering] = useState(false);
  const [progress, setProgress] = useState(0);
  const [taskId, setTaskId] = useState(null);
  const [logs, setLogs] = useState([]);
  const [renderResult, setRenderResult] = useState(null);

  useEffect(() => {
    loadProjects();
  }, []);

  useEffect(() => {
    if (initialProjectId) setSelectedProjectId(initialProjectId);
  }, [initialProjectId]);

  useEffect(() => {
    if (selectedProjectId) {
      loadProjectDetails(selectedProjectId);
    }
  }, [selectedProjectId]);

  const loadProjects = async () => {
    try {
      const res = await fetch('/api/v1/projects/?page=1&page_size=50');
      if (res.ok) {
        const data = await res.json();
        setProjects(data.items || []);
        if (!selectedProjectId && data.items?.length > 0) {
          setSelectedProjectId(data.items[0].id);
        }
      }
    } catch (e) {
      console.error(e);
    }
  };

  const loadProjectDetails = async (projId) => {
    try {
      const res = await fetch(`/api/v1/projects/${projId}`);
      if (res.ok) {
        const data = await res.json();
        setProjectInfo(data.project);
      }
    } catch (e) {
      console.error(e);
    }
  };

  // Poll Compose Task
  useEffect(() => {
    let interval = null;
    if (isRendering && taskId) {
      interval = setInterval(async () => {
        try {
          const res = await fetch(`/api/v1/composer/status/${taskId}`);
          if (!res.ok) return;
          const data = await res.json();
          setProgress(data.progress || 0);
          if (data.logs) setLogs(data.logs);

          if (data.status === 'completed') {
            setIsRendering(false);
            clearInterval(interval);
            setRenderResult(data.result);
            loadProjectDetails(selectedProjectId);
            alert('🎉 Video thành phẩm đã render hoàn tất!');
          } else if (data.status === 'failed') {
            setIsRendering(false);
            clearInterval(interval);
            alert(`Lỗi Render: ${data.error}`);
          }
        } catch (e) {
          console.error(e);
        }
      }, 1200);
    }
    return () => clearInterval(interval);
  }, [isRendering, taskId]);

  const handleStartCompose = async () => {
    if (!selectedProjectId) return;
    setIsRendering(true);
    setProgress(5);
    setLogs([]);
    setRenderResult(null);

    try {
      const res = await fetch('/api/v1/composer/compose', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: selectedProjectId,
          logo_position: logoPosition,
          logo_size: logoSize,
          logo_opacity: 0.90,
          channel_name: channelName,
          channel_opacity: channelOpacity,
          karaoke_highlight_color: karaokeColor,
          backdrop_opacity_hex: backdropOpacity,
          has_mask: true,
          crop_ratio: '16:9',
          margin_v: marginV,
          font_size: fontSize,
          box_style: boxStyle,
          box_padding: boxPadding
        })
      });

      if (!res.ok) throw new Error('Không thể khởi chạy Render Video');
      const data = await res.json();
      setTaskId(data.task_id);
    } catch (e) {
      setIsRendering(false);
      alert(`Lỗi: ${e.message}`);
    }
  };

  return (
    <div className="tab-container" style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
      
      {/* Top Header & Settings */}
      <div className="glass-panel" style={{ padding: '20px', borderRadius: 'var(--radius-md)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '16px' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '6px' }}>
              <span className="tag-badge sunset" style={{ padding: '4px 10px', fontSize: '11px', fontWeight: 800 }}>
                🎬 VIDEO COMPOSER STUDIO
              </span>
              <span className="tag-badge emerald" style={{ padding: '4px 10px', fontSize: '11px', fontWeight: 800 }}>
                1-PASS HARDWARE ACCELERATION
              </span>
            </div>
            <h2 style={{ fontSize: '20px', fontWeight: 800, color: 'var(--text-main)' }}>
              Xuất Bản Video Review Hoàn Chỉnh
            </h2>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <select
              value={selectedProjectId || ''}
              onChange={(e) => setSelectedProjectId(parseInt(e.target.value, 10))}
              style={{
                padding: '10px 14px',
                background: 'var(--bg-input)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-sm)',
                color: 'var(--text-main)',
                fontSize: '13px',
                fontWeight: 600,
                maxWidth: '320px'
              }}
            >
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  #{p.id} - {p.title}
                </option>
              ))}
            </select>

            <button
              className="btn btn-primary"
              onClick={handleStartCompose}
              disabled={isRendering || !selectedProjectId}
              style={{ background: 'var(--grad-sunset)', boxShadow: '0 0 25px rgba(255, 8, 68, 0.4)', fontWeight: 800 }}
            >
              {isRendering ? (
                <>
                  <RefreshCw size={15} className="animate-spin" /> Đang Render ({progress}%)...
                </>
              ) : (
                <>
                  <Film size={15} /> Render Video Hoàn Chỉnh
                </>
              )}
            </button>
          </div>
        </div>
      </div>

      {/* Grid: Canvas Customizer | Live Logs & Download */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.1fr 0.9fr', gap: '20px' }}>
        
        {/* Left Column: Visual Placement & Video Controls */}
        <div className="glass-panel" style={{ padding: '20px', borderRadius: 'var(--radius-md)', display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <h3 style={{ fontSize: '15px', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Sliders size={16} color="var(--cyan)" /> Cấu Hình Vị Trí Subtitle & Logo
          </h3>

          {/* Interactive Screen Preview */}
          <div style={{
            position: 'relative',
            width: '100%',
            height: '240px',
            background: '#070913',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border)',
            overflow: 'hidden',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center'
          }}>
            {/* Background Graphic */}
            <div style={{ position: 'absolute', inset: 0, opacity: 0.2, background: 'radial-gradient(circle at center, #334155 0%, #090d16 100%)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Film size={56} color="#64748b" />
            </div>

            {/* Corner Logo */}
            <div style={{
              position: 'absolute',
              top: logoPosition.includes('top') ? '16px' : 'auto',
              bottom: logoPosition.includes('bottom') ? '16px' : 'auto',
              left: logoPosition.includes('left') ? '16px' : 'auto',
              right: logoPosition.includes('right') ? '16px' : 'auto',
              padding: '6px 12px',
              background: 'rgba(233,64,87,0.85)',
              borderRadius: '6px',
              fontSize: '11px',
              fontWeight: 800,
              color: '#fff',
              boxShadow: '0 4px 12px rgba(0,0,0,0.5)'
            }}>
              LOGO NÈN GIA
            </div>

            {/* Channel Watermark */}
            <div style={{
              position: 'absolute',
              top: '16px',
              right: '16px',
              fontSize: '12px',
              fontWeight: 700,
              color: `rgba(255,255,255,${channelOpacity})`,
              letterSpacing: '0.5px'
            }}>
              {channelName}
            </div>

            {/* Dynamic Subtitle Target Box */}
            <div style={{
              position: 'absolute',
              bottom: `${marginV}px`,
              left: '24px',
              right: '24px',
              padding: `${boxPadding}px 18px`,
              background: boxStyle === 'white_box' 
                ? '#FFFFFF' 
                : boxStyle === 'dark_box' 
                  ? `rgba(0, 0, 0, ${parseInt(backdropOpacity, 16) / 255})` 
                  : 'transparent',
              backdropFilter: boxStyle === 'dark_box' ? 'blur(6px)' : 'none',
              border: boxStyle === 'white_box' 
                ? '1.5px solid #CBD5E1' 
                : boxStyle === 'dark_box' 
                  ? '1px solid rgba(0, 242, 254, 0.4)' 
                  : 'none',
              borderRadius: boxStyle === 'white_box' ? '10px' : '8px',
              textAlign: 'center',
              boxShadow: boxStyle === 'white_box' 
                ? '0 4px 20px rgba(0,0,0,0.35)' 
                : boxStyle === 'dark_box' 
                  ? '0 4px 20px rgba(0,0,0,0.7)' 
                  : 'none',
              transition: 'all 0.2s ease'
            }}>
              <div style={{ 
                fontSize: `${Math.max(12, Math.round(fontSize * 0.75))}px`, 
                fontWeight: 800, 
                color: boxStyle === 'white_box' ? '#0F172A' : '#FFFFFF',
                textShadow: boxStyle === 'outline_only' 
                  ? '0 1px 3px #000, 0 0 2px #000' 
                  : 'none',
                letterSpacing: '0.2px'
              }}>
                <span style={{ 
                  color: '#EF4444',
                  fontWeight: 900
                }}>Lúc này</span> hắn mới nhận ra điều bất thường... ({fontSize}px, Hộp {boxPadding}px)
              </div>
            </div>
          </div>

          {/* Box Style Selector */}
          <div style={{ marginBottom: '14px' }}>
            <label style={{ fontSize: '12px', color: 'var(--text-sub)', display: 'block', marginBottom: '6px' }}>
              Kiểu Khung Phụ Đề & Che Chữ Gốc:
            </label>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '8px' }}>
              {[
                { id: 'white_box', l: '⚪ Khung Trắng Chữ Đen (Khuyên dùng)', desc: 'Che sạch 100% chữ gốc' },
                { id: 'dark_box', l: '⚫ Hộp Đen Mờ Chữ Vàng', desc: 'Chuẩn điện ảnh Netflix' },
                { id: 'outline_only', l: '🔤 Chữ Viền Trong Suốt', desc: 'Không có hộp che' }
              ].map(st => (
                <button
                  key={st.id}
                  type="button"
                  onClick={() => setBoxStyle(st.id)}
                  style={{
                    padding: '8px 10px',
                    borderRadius: '8px',
                    cursor: 'pointer',
                    textAlign: 'left',
                    background: boxStyle === st.id ? 'rgba(0, 242, 254, 0.15)' : 'rgba(255, 255, 255, 0.04)',
                    border: boxStyle === st.id ? '1.5px solid var(--cyan)' : '1px solid var(--border)',
                    color: boxStyle === st.id ? 'var(--cyan)' : 'var(--text-main)',
                    transition: 'all 0.15s ease'
                  }}
                >
                  <div style={{ fontSize: '12px', fontWeight: 800 }}>{st.l}</div>
                  <div style={{ fontSize: '10px', color: 'var(--text-dim)', marginTop: '2px' }}>{st.desc}</div>
                </button>
              ))}
            </div>
          </div>

          {/* Controls Grid */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '14px' }}>
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                <label style={{ fontSize: '12px', color: 'var(--text-sub)' }}>Độ To Hộp Che Dọc (Padding):</label>
                <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                  <input
                    type="number"
                    min="3"
                    max="30"
                    value={boxPadding}
                    onChange={(e) => setBoxPadding(parseInt(e.target.value, 10) || 8)}
                    style={{ width: '55px', padding: '2px 6px', background: 'var(--bg-input)', border: '1px solid var(--border)', borderRadius: '4px', color: 'var(--cyan)', fontSize: '11px', textAlign: 'center', fontWeight: 700 }}
                  />
                  <span style={{ fontSize: '11px', color: 'var(--text-dim)' }}>px</span>
                </div>
              </div>
              <div style={{ display: 'flex', gap: '6px' }}>
                {[
                  { v: 6, l: '6px (Gọn)' },
                  { v: 8, l: '8px (Chuẩn)' },
                  { v: 12, l: '12px (Vừa)' },
                  { v: 16, l: '16px (Dày che hết)' }
                ].map((lvl) => (
                  <button
                    key={lvl.v}
                    type="button"
                    onClick={() => setBoxPadding(lvl.v)}
                    style={{
                      flex: 1,
                      padding: '6px 4px',
                      fontSize: '11px',
                      fontWeight: 700,
                      borderRadius: '6px',
                      cursor: 'pointer',
                      background: boxPadding === lvl.v ? 'rgba(0, 242, 254, 0.2)' : 'rgba(255, 255, 255, 0.05)',
                      border: boxPadding === lvl.v ? '1px solid var(--cyan)' : '1px solid var(--border)',
                      color: boxPadding === lvl.v ? 'var(--cyan)' : 'var(--text-sub)',
                      transition: 'all 0.15s ease'
                    }}
                  >
                    {lvl.l}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                <label style={{ fontSize: '12px', color: 'var(--text-sub)' }}>Độ cao Sub (Margin V):</label>
                <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                  <input
                    type="number"
                    min="6"
                    max="100"
                    value={marginV}
                    onChange={(e) => setMarginV(parseInt(e.target.value, 10) || 12)}
                    style={{ width: '55px', padding: '2px 6px', background: 'var(--bg-input)', border: '1px solid var(--border)', borderRadius: '4px', color: 'var(--cyan)', fontSize: '11px', textAlign: 'center', fontWeight: 700 }}
                  />
                  <span style={{ fontSize: '11px', color: 'var(--text-dim)' }}>px</span>
                </div>
              </div>
              <div style={{ display: 'flex', gap: '6px' }}>
                {[
                  { v: 8, l: '8px (Sát đáy)' },
                  { v: 12, l: '12px' },
                  { v: 16, l: '16px' },
                  { v: 24, l: '24px' }
                ].map((lvl) => (
                  <button
                    key={lvl.v}
                    type="button"
                    onClick={() => setMarginV(lvl.v)}
                    style={{
                      flex: 1,
                      padding: '6px 4px',
                      fontSize: '11px',
                      fontWeight: 700,
                      borderRadius: '6px',
                      cursor: 'pointer',
                      background: marginV === lvl.v ? 'rgba(0, 242, 254, 0.2)' : 'rgba(255, 255, 255, 0.05)',
                      border: marginV === lvl.v ? '1px solid var(--cyan)' : '1px solid var(--border)',
                      color: marginV === lvl.v ? 'var(--cyan)' : 'var(--text-sub)',
                      transition: 'all 0.15s ease'
                    }}
                  >
                    {lvl.l}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                <label style={{ fontSize: '12px', color: 'var(--text-sub)' }}>Cỡ Chữ Phụ Đề (Font Size):</label>
                <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                  <input
                    type="number"
                    min="9"
                    max="32"
                    value={fontSize}
                    onChange={(e) => setFontSize(parseInt(e.target.value, 10) || 13)}
                    style={{ width: '55px', padding: '2px 6px', background: 'var(--bg-input)', border: '1px solid var(--border)', borderRadius: '4px', color: 'var(--cyan)', fontSize: '11px', textAlign: 'center', fontWeight: 700 }}
                  />
                  <span style={{ fontSize: '11px', color: 'var(--text-dim)' }}>px</span>
                </div>
              </div>
              <div style={{ display: 'flex', gap: '6px' }}>
                {[
                  { v: 11, l: '11px (Siêu bé)' },
                  { v: 13, l: '13px (Gọn)' },
                  { v: 16, l: '16px' },
                  { v: 20, l: '20px' }
                ].map((lvl) => (
                  <button
                    key={lvl.v}
                    type="button"
                    onClick={() => setFontSize(lvl.v)}
                    style={{
                      flex: 1,
                      padding: '6px 4px',
                      fontSize: '11px',
                      fontWeight: 700,
                      borderRadius: '6px',
                      cursor: 'pointer',
                      background: fontSize === lvl.v ? 'rgba(0, 242, 254, 0.2)' : 'rgba(255, 255, 255, 0.05)',
                      border: fontSize === lvl.v ? '1px solid var(--cyan)' : '1px solid var(--border)',
                      color: fontSize === lvl.v ? 'var(--cyan)' : 'var(--text-sub)',
                      transition: 'all 0.15s ease'
                    }}
                  >
                    {lvl.l}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label style={{ fontSize: '12px', color: 'var(--text-sub)', display: 'block', marginBottom: '4px' }}>
                Vị Trí Logo Góc:
              </label>
              <select
                value={logoPosition}
                onChange={(e) => setLogoPosition(e.target.value)}
                style={{ width: '100%', padding: '6px 10px', background: 'var(--bg-input)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', color: 'var(--text-main)', fontSize: '12px' }}
              >
                <option value="top_left">Góc Trên Trái (Mặc định)</option>
                <option value="top_right">Góc Trên Phải</option>
                <option value="bottom_left">Góc Dưới Trái</option>
                <option value="bottom_right">Góc Dưới Phải</option>
              </select>
            </div>

            <div>
              <label style={{ fontSize: '12px', color: 'var(--text-sub)', display: 'block', marginBottom: '4px' }}>
                Tên Kênh Watermark:
              </label>
              <input
                type="text"
                value={channelName}
                onChange={(e) => setChannelName(e.target.value)}
                style={{ width: '100%', padding: '6px 10px', background: 'var(--bg-input)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', color: 'var(--text-main)', fontSize: '12px' }}
              />
            </div>

            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                <label style={{ fontSize: '12px', color: 'var(--text-sub)' }}>Độ mờ Watermark:</label>
                <strong style={{ fontSize: '12px', color: 'var(--amber)' }}>{Math.round(channelOpacity * 100)}%</strong>
              </div>
              <div style={{ display: 'flex', gap: '6px' }}>
                {[
                  { v: 0.3, l: '30%' },
                  { v: 0.5, l: '50%' },
                  { v: 0.75, l: '75%' },
                  { v: 1.0, l: '100%' }
                ].map((lvl) => (
                  <button
                    key={lvl.v}
                    type="button"
                    onClick={() => setChannelOpacity(lvl.v)}
                    style={{
                      flex: 1,
                      padding: '6px 4px',
                      fontSize: '11px',
                      fontWeight: 700,
                      borderRadius: '6px',
                      cursor: 'pointer',
                      background: Math.abs(channelOpacity - lvl.v) < 0.05 ? 'rgba(245, 158, 11, 0.25)' : 'rgba(255, 255, 255, 0.05)',
                      border: Math.abs(channelOpacity - lvl.v) < 0.05 ? '1px solid var(--amber)' : '1px solid var(--border)',
                      color: Math.abs(channelOpacity - lvl.v) < 0.05 ? 'var(--amber)' : 'var(--text-sub)',
                      transition: 'all 0.15s ease'
                    }}
                  >
                    {lvl.l}
                  </button>
                ))}
              </div>
            </div>
          </div>
          </div>
        </div>

        {/* Right Column: Progress & Video Player */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          
          {/* Progress & Terminal */}
          <div className="glass-panel" style={{ padding: '20px', borderRadius: 'var(--radius-md)' }}>
            <h3 style={{ fontSize: '15px', fontWeight: 700, marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '8px' }}>
              <Terminal size={16} color="var(--cyan)" /> Tiến Trình Render FFmpeg
            </h3>

            <div style={{ width: '100%', height: '8px', background: 'var(--bg-input)', borderRadius: '4px', overflow: 'hidden', marginBottom: '14px' }}>
              <div style={{
                width: `${progress}%`,
                height: '100%',
                background: 'var(--grad-sunset)',
                transition: 'width 0.4s ease'
              }} />
            </div>

            <div style={{
              height: '140px',
              overflowY: 'auto',
              background: '#050711',
              padding: '10px 12px',
              borderRadius: '6px',
              fontFamily: 'var(--font-mono)',
              fontSize: '11px',
              lineHeight: 1.6,
              color: '#94a3b8'
            }}>
              {logs.length === 0 ? (
                <div style={{ color: '#475569' }}>Sẵn sàng. Nhấn [Render Video Hoàn Chỉnh] để bắt đầu xuất file.</div>
              ) : (
                logs.map((l, idx) => (
                  <div key={idx} style={{
                    color: l.type === 'emerald' ? '#34d399' : l.type === 'cyan' ? '#38bdf8' : l.type === 'rose' ? '#f43f5e' : '#cbd5e1'
                  }}>
                    [{l.time}] {l.text}
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Download & Final Video Action */}
          <div className="glass-panel" style={{ padding: '20px', borderRadius: 'var(--radius-md)' }}>
            <h3 style={{ fontSize: '15px', fontWeight: 700, marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '8px' }}>
              <CheckCircle2 size={16} color="var(--emerald)" /> Video Thành Phẩm
            </h3>

            {projectInfo?.final_video_path || renderResult?.final_video_path ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                <div style={{ padding: '12px', background: 'rgba(16,185,129,0.1)', border: '1px solid rgba(16,185,129,0.3)', borderRadius: 'var(--radius-sm)', fontSize: '13px', color: 'var(--emerald)' }}>
                  ✔ Video đã sẵn sàng! Đã mix BGM ducking, phụ đề karaoke & logo.
                </div>

                <a
                  href={`/api/v1/projects/download-video/${selectedProjectId}`}
                  download
                  className="btn btn-primary"
                  style={{ textDecoration: 'none', textAlign: 'center', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px', padding: '12px', background: 'var(--grad-emerald)', color: '#070913', fontWeight: 800 }}
                >
                  <Download size={16} /> Tải Video Hoàn Chỉnh (.mp4)
                </a>
              </div>
            ) : (
              <div style={{ color: 'var(--text-dim)', fontSize: '13px' }}>
                Chưa có bản render cho project này. Hãy bấm "Render Video Hoàn Chỉnh" ở trên để tạo.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
