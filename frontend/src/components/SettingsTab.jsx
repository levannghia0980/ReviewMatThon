import React, { useState, useEffect } from 'react';
import { 
  FolderDown, 
  Database, 
  Cpu, 
  ShieldCheck, 
  Zap, 
  KeyRound, 
  CloudLightning, 
  CheckCircle2, 
  AlertCircle, 
  Loader2, 
  Eye, 
  EyeOff, 
  Save, 
  ExternalLink,
  Sparkles,
  Bot,
  Sliders,
  Layers,
  Volume2,
  RefreshCw,
  Gauge,
  Flame,
  Rocket
} from 'lucide-react';

export default function SettingsTab() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testingGroq, setTestingGroq] = useState(false);
  const [testingGemini, setTestingGemini] = useState(false);
  
  // API Keys Reveal State
  const [showGeminiKey, setShowGeminiKey] = useState(false);
  const [showGroqKey, setShowGroqKey] = useState(false);
  const [showOpenRouterKey, setShowOpenRouterKey] = useState(false);

  // Form State
  const [geminiKey, setGeminiKey] = useState('');
  const [geminiModel, setGeminiModel] = useState('gemini-3.1-flash-lite');
  
  const [groqKey, setGroqKey] = useState('');
  const [groqModel, setGroqModel] = useState('whisper-large-v3');
  const [asrEngine, setAsrEngine] = useState('capcut');
  const [whisperSize, setWhisperSize] = useState('base');

  const [openrouterKey, setOpenrouterKey] = useState('');
  
  // Translation Batch Settings
  const [translationMaxChars, setTranslationMaxChars] = useState(50000);
  const [translationBatchSize, setTranslationBatchSize] = useState(300);
  
  // TTS Engine & Concurrency (CapCut vs TikTok, Up to 128 threads)
  const [ttsEngine, setTtsEngine] = useState('capcut');
  const [ttsThreads, setTtsThreads] = useState(128);
  const [capcutCookie, setCapcutCookie] = useState('');
  const [tiktokSessionId, setTiktokSessionId] = useState('410bfa37bdc185e1c6da82e1afb48409');

  // Status & Feedback
  const [testResult, setTestResult] = useState(null);
  const [geminiTestResult, setGeminiTestResult] = useState(null);
  const [saveMessage, setSaveMessage] = useState(null);

  useEffect(() => {
    fetchSettings();
  }, []);

  const fetchSettings = async () => {
    try {
      setLoading(true);
      const res = await fetch('/api/v1/settings');
      if (res.ok) {
        const data = await res.json();
        
        // Đồng bộ 2 chiều với LocalStorage để chống mất Key vĩnh viễn
        const localGemini = localStorage.getItem('saved_gemini_key') || '';
        const localGroq = localStorage.getItem('saved_groq_key') || '';
        const localOpenRouter = localStorage.getItem('saved_openrouter_key') || '';

        const finalGemini = data.gemini_api_key || localGemini || '';
        const finalGroq = data.groq_api_key || localGroq || '';
        const finalOpenRouter = data.openrouter_api_key || localOpenRouter || '';

        setGeminiKey(finalGemini);
        setGeminiModel(data.gemini_model || 'gemini-3.1-flash-lite');
        setGroqKey(finalGroq);
        setGroqModel(data.groq_model || 'whisper-large-v3');
        setAsrEngine(data.asr_engine || 'groq');
        setWhisperSize(data.whisper_model_size || 'base');
        setOpenrouterKey(finalOpenRouter);
        setTranslationMaxChars(data.translation_max_chars || 50000);
        setTranslationBatchSize(data.translation_batch_size || 300);
        setTtsThreads(data.tts_threads || 128);
        setTtsEngine(data.tts_engine || 'capcut');
        setCapcutCookie(data.capcut_cookie || '');
        setTiktokSessionId(data.tiktok_session_id || '410bfa37bdc185e1c6da82e1afb48409');

        if (finalGemini) localStorage.setItem('saved_gemini_key', finalGemini);
        if (finalGroq) localStorage.setItem('saved_groq_key', finalGroq);
        if (finalOpenRouter) localStorage.setItem('saved_openrouter_key', finalOpenRouter);
      }
    } catch (err) {
      console.error('Lỗi khi tải cài đặt:', err);
      // Fallback lấy từ localStorage nếu mất kết nối server
      const localGemini = localStorage.getItem('saved_gemini_key') || '';
      const localGroq = localStorage.getItem('saved_groq_key') || '';
      if (localGemini) setGeminiKey(localGemini);
      if (localGroq) setGroqKey(localGroq);
    } finally {
      setLoading(false);
    }
  };

  const handleTestGemini = async () => {
    const rawKey = geminiKey.trim();
    if (!rawKey) {
      setGeminiTestResult({ success: false, message: 'Vui lòng nhập Google Gemini API Key trước khi kiểm tra!' });
      return;
    }
    setTestingGemini(true);
    setGeminiTestResult(null);
    try {
      const res = await fetch('/api/v1/settings/test-gemini', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: rawKey })
      });
      const data = await res.json();
      if (res.ok) {
        setGeminiTestResult({
          success: true,
          message: data.message || '🎉 Kết nối Google Gemini API thành công! API Key hợp lệ và hoạt động tốt.'
        });
        localStorage.setItem('saved_gemini_key', rawKey);
      } else {
        setGeminiTestResult({ success: false, message: data.detail || 'Không thể kết nối Google Gemini API.' });
      }
    } catch (err) {
      setGeminiTestResult({ success: false, message: 'Lỗi mạng khi kết nối máy chủ kiểm tra API.' });
    } finally {
      setTestingGemini(false);
    }
  };

  const handleTestGroq = async () => {
    setTestingGroq(true);
    setTestResult(null);
    try {
      const res = await fetch('/api/v1/settings/test-groq', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: groqKey.trim() || undefined })
      });
      const data = await res.json();
      if (res.ok) {
        setTestResult({ success: true, message: data.message || 'Kết nối Groq thành công! API Key hợp lệ.' });
        if (groqKey.trim()) localStorage.setItem('saved_groq_key', groqKey.trim());
      } else {
        setTestResult({ success: false, message: data.detail || 'Không thể kết nối Groq API.' });
      }
    } catch (err) {
      setTestResult({ success: false, message: 'Lỗi mạng khi kết nối máy chủ.' });
    } finally {
      setTestingGroq(false);
    }
  };

  const handleSaveSettings = async (e) => {
    if (e) e.preventDefault();
    setSaving(true);
    setSaveMessage(null);
    try {
      const gK = geminiKey.trim();
      const grK = groqKey.trim();
      const orK = openrouterKey.trim();

      // Lưu ngay vào localStorage
      if (gK) localStorage.setItem('saved_gemini_key', gK);
      if (grK) localStorage.setItem('saved_groq_key', grK);
      if (orK) localStorage.setItem('saved_openrouter_key', orK);

      const payload = {
        gemini_api_key: gK,
        gemini_model: geminiModel,
        groq_api_key: grK,
        groq_model: groqModel,
        asr_engine: asrEngine,
        whisper_model_size: whisperSize,
        openrouter_api_key: orK,
        translation_max_chars: parseInt(translationMaxChars, 10) || 50000,
        translation_batch_size: parseInt(translationBatchSize, 10) || 300,
        tts_threads: parseInt(ttsThreads, 10) || 128,
        tts_engine: ttsEngine,
        capcut_cookie: capcutCookie.trim(),
        tiktok_session_id: tiktokSessionId.trim()
      };

      const res = await fetch('/api/v1/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (res.ok) {
        setSaveMessage({ success: true, text: '🎉 Đã lưu toàn bộ cấu hình vào file .env và bộ nhớ trình duyệt an toàn!' });
        setTimeout(() => setSaveMessage(null), 4000);
      } else {
        setSaveMessage({ success: false, text: data.detail || 'Lỗi khi lưu cấu hình.' });
      }
    } catch (err) {
      setSaveMessage({ success: false, text: 'Lỗi mạng khi gửi yêu cầu.' });
    } finally {
      setSaving(false);
    }
  };

  const geminiModelsList = [
    {
      id: 'gemini-3.1-flash-lite',
      title: '⚡ Gemini 3.1 Flash Lite',
      badge: 'Khuyên Dùng - Siêu Tốc',
      desc: 'Tốc độ phản hồi nhanh nhất, tiết kiệm quota API, dịch mượt mà không độ trễ.'
    },
    {
      id: 'gemini-3.5-flash-lite',
      title: '🚀 Gemini 3.5 Flash Lite',
      badge: 'Cân Bằng Chuẩn',
      desc: 'Cân bằng giữa tốc độ cực nhanh và khả năng hiểu ngữ cảnh phim phức tạp.'
    },
    {
      id: 'gemini-3.5-flash',
      title: '🧠 Gemini 3.5 Flash',
      badge: 'Chất Lượng Cao Nhất',
      desc: 'Trí tuệ nhân tạo mạnh mẽ nhất, xử lý câu văn hoa mỹ, tối ưu hóa triệt để xưng hô.'
    }
  ];

  const batchSentencesTiers = [
    { value: 150, label: '150 Câu / Lô', sub: 'Lô vừa (~5-7 phút phim)' },
    { value: 250, label: '250 Câu / Lô', sub: 'Lô chuẩn (~10-15 phút)' },
    { value: 300, label: '300 Câu / Lô', sub: 'Chuẩn AIRead (Khuyên dùng)' },
    { value: 400, label: '400 Câu / Lô', sub: 'Lô lớn (~20-25 phút)' },
    { value: 500, label: '500 Câu / Lô', sub: 'Siêu Lớn (Dịch 1-2 lần là xong)' }
  ];

  const ttsThreadsTiers = [
    { value: 16, label: '16 Luồng', tag: 'Cơ Bản', desc: 'Nhẹ máy, ổn định' },
    { value: 32, label: '32 Luồng', tag: 'Nhanh', desc: 'Tạo voiceover trong ~10s' },
    { value: 64, label: '64 Luồng', tag: 'Chuẩn Studio', desc: 'Khuyên dùng, siêu tốc độ' },
    { value: 96, label: '96 Luồng', tag: 'Cực Nhanh', desc: 'Tạo 100 câu chỉ trong ~4s' },
    { value: 128, label: '128 Luồng', tag: '🚀 Max Speed', desc: 'Tốc độ tối đa, xong tức thì' }
  ];

  return (
    <div style={{ padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: '20px', maxWidth: '1480px', margin: '0 auto', width: '100%', boxSizing: 'border-box' }}>
      
      {/* Header Banner */}
      <div className="glass-panel" style={{
        background: 'linear-gradient(135deg, rgba(16,22,45,0.9) 0%, rgba(30,25,60,0.8) 100%)',
        border: '1px solid rgba(0, 242, 254, 0.25)',
        padding: '20px 24px',
        borderRadius: '10px',
        boxShadow: 'var(--shadow-neon)'
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '16px' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '8px' }}>
              <span className="tag-badge cyan" style={{ padding: '4px 10px', fontSize: '11px', fontWeight: 800 }}>
                ⚙️ SYSTEM CONFIGURATION
              </span>
              <span className="tag-badge emerald" style={{ padding: '4px 10px', fontSize: '11px', fontWeight: 800 }}>
                ⚡ 1-CLICK PRESETS
              </span>
            </div>
            <h1 style={{ fontSize: '22px', fontWeight: 800, color: 'var(--text-main)', marginBottom: '6px' }}>
              Cài Đặt Hệ Thống & AI Studio
            </h1>
            <p style={{ color: 'var(--text-sub)', fontSize: '13px', maxWidth: '700px', margin: 0 }}>
              Chọn nhanh các mức cấu hình bằng nút bấm 1 chạm (không cần kéo thanh trượt). Quản lý API Key, chọn mô hình Gemini và luồng song song TikTok lên tới 128 luồng.
            </p>
          </div>

          <button 
            type="button"
            onClick={handleSaveSettings}
            disabled={saving}
            className="btn btn-primary"
            style={{
              padding: '12px 26px',
              fontSize: '14px',
              fontWeight: 800,
              background: 'var(--grad-cyan)',
              color: '#070913',
              boxShadow: '0 0 20px rgba(0, 242, 254, 0.4)',
              display: 'flex',
              alignItems: 'center',
              gap: '8px'
            }}
          >
            {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
            LƯU TẤT CẢ CÀI ĐẶT
          </button>
        </div>
      </div>

      {saveMessage && (
        <div style={{
          padding: '12px 18px',
          borderRadius: '10px',
          fontSize: '13px',
          fontWeight: 700,
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
          background: saveMessage.success ? 'rgba(16, 185, 129, 0.15)' : 'rgba(244, 63, 94, 0.15)',
          border: `1px solid ${saveMessage.success ? 'var(--emerald)' : 'var(--rose)'}`,
          color: saveMessage.success ? 'var(--emerald)' : 'var(--rose)',
          boxShadow: '0 4px 15px rgba(0,0,0,0.2)'
        }}>
          {saveMessage.success ? <CheckCircle2 size={18} /> : <AlertCircle size={18} />}
          {saveMessage.text}
        </div>
      )}

      {loading ? (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '60px', gap: '12px', color: 'var(--text-sub)' }}>
          <Loader2 size={28} className="animate-spin" /> Đang tải dữ liệu cấu hình hệ thống...
        </div>
      ) : (
        <form onSubmit={handleSaveSettings} style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
          
          {/* ========================================================================= */}
          {/* SECTION 1: GOOGLE GEMINI AI TRANSLATION (3.1 / 3.5 MODELS + BATCH CHARS) */}
          {/* ========================================================================= */}
          <div className="glass-panel" style={{
            padding: '24px',
            borderRadius: 'var(--radius-md)',
            border: '1px solid rgba(56, 189, 248, 0.3)',
            display: 'flex',
            flexDirection: 'column',
            gap: '20px'
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <div style={{ width: '32px', height: '32px', borderRadius: '8px', background: 'rgba(56, 189, 248, 0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <Bot size={18} color="var(--cyan)" />
                </div>
                <div>
                  <h3 style={{ fontSize: '16px', fontWeight: 800, color: 'var(--text-main)', margin: 0 }}>
                    1. Cấu Hình Dịch Thuật Google Gemini AI
                  </h3>
                  <p style={{ fontSize: '12px', color: 'var(--text-sub)', margin: '2px 0 0 0' }}>
                    Dịch chuẩn văn phong kiếm hiệp/tu tiên, bảo toàn xưng hô và quét sạch Hán ngữ
                  </p>
                </div>
              </div>

              <a
                href="https://aistudio.google.com/app/apikey"
                target="_blank"
                rel="noreferrer"
                style={{
                  fontSize: '12px',
                  color: 'var(--cyan)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px',
                  textDecoration: 'none',
                  fontWeight: 700,
                  background: 'rgba(56, 189, 248, 0.1)',
                  padding: '5px 10px',
                  borderRadius: '6px',
                  border: '1px solid rgba(56, 189, 248, 0.25)'
                }}
              >
                Lấy Gemini API Key Miễn Phí <ExternalLink size={12} />
              </a>
            </div>

            {/* Gemini API Key Input & Test Button */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px', flexWrap: 'wrap', gap: '8px' }}>
                <label style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-main)', margin: 0 }}>
                  Google Gemini API Key (Bắt đầu bằng AIzaSy...):
                </label>
                <button
                  type="button"
                  onClick={handleTestGemini}
                  disabled={testingGemini || !geminiKey.trim()}
                  style={{
                    padding: '6px 14px',
                    fontSize: '12px',
                    fontWeight: 700,
                    borderRadius: '6px',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    background: 'linear-gradient(135deg, rgba(0, 242, 254, 0.25) 0%, rgba(79, 172, 254, 0.25) 100%)',
                    border: '1px solid var(--cyan)',
                    color: 'var(--cyan)',
                    cursor: (testingGemini || !geminiKey.trim()) ? 'not-allowed' : 'pointer',
                    boxShadow: '0 0 12px rgba(0, 242, 254, 0.25)',
                    transition: 'all 0.2s ease'
                  }}
                >
                  {testingGemini ? <Loader2 size={14} className="animate-spin" /> : <Zap size={14} />}
                  {testingGemini ? 'Đang Kiểm Tra Key...' : '⚡ Kiểm Tra Key Gemini'}
                </button>
              </div>

              <div style={{ position: 'relative' }}>
                <input
                  type={showGeminiKey ? 'text' : 'password'}
                  placeholder="Dán Google Gemini API Key (AIzaSy...) vào đây..."
                  value={geminiKey}
                  onChange={(e) => setGeminiKey(e.target.value)}
                  style={{
                    width: '100%',
                    padding: '12px 42px 12px 14px',
                    background: 'var(--bg-input)',
                    border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-sm)',
                    color: 'var(--text-main)',
                    fontSize: '13px',
                    fontFamily: 'var(--font-mono)'
                  }}
                />
                <button
                  type="button"
                  onClick={() => setShowGeminiKey(!showGeminiKey)}
                  style={{
                    position: 'absolute',
                    right: '12px',
                    top: '50%',
                    transform: 'translateY(-50%)',
                    background: 'none',
                    border: 'none',
                    color: 'var(--text-dim)',
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center'
                  }}
                  title={showGeminiKey ? 'Ẩn API Key' : 'Hiện API Key'}
                >
                  {showGeminiKey ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>

              {/* Warning if user pasted Groq key into Gemini */}
              {geminiKey.trim().startsWith('gsk_') && (
                <div style={{
                  marginTop: '8px',
                  padding: '8px 12px',
                  borderRadius: '6px',
                  fontSize: '12px',
                  background: 'rgba(244, 63, 94, 0.15)',
                  border: '1px solid var(--rose)',
                  color: 'var(--rose)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px'
                }}>
                  <AlertCircle size={14} />
                  <span><strong>Nhầm loại Key:</strong> Bạn đang nhập Groq API Key (<code>gsk_...</code>). Google Gemini API Key phải bắt đầu bằng <code>AIzaSy...</code>.</span>
                </div>
              )}

              {/* Gemini Test Feedback Result */}
              {geminiTestResult && (
                <div style={{
                  marginTop: '10px',
                  padding: '10px 14px',
                  borderRadius: '6px',
                  fontSize: '12px',
                  fontWeight: 600,
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  background: geminiTestResult.success ? 'rgba(16, 185, 129, 0.15)' : 'rgba(244, 63, 94, 0.15)',
                  border: `1px solid ${geminiTestResult.success ? 'var(--emerald)' : 'var(--rose)'}`,
                  color: geminiTestResult.success ? 'var(--emerald)' : 'var(--rose)'
                }}>
                  {geminiTestResult.success ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}
                  {geminiTestResult.message}
                </div>
              )}
            </div>

            {/* 1A. Mức Chọn Mô Hình Gemini (Card Buttons Bấm Chọn 1 Chạm) */}
            <div>
              <label style={{ display: 'block', fontSize: '12px', fontWeight: 700, color: 'var(--text-main)', marginBottom: '10px' }}>
                Chọn Mô Hình Dịch Thuật Gemini (Bấm chọn mức):
              </label>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '12px' }}>
                {geminiModelsList.map((m) => {
                  const isSelected = geminiModel === m.id;
                  return (
                    <div
                      key={m.id}
                      onClick={() => setGeminiModel(m.id)}
                      style={{
                        padding: '16px',
                        borderRadius: '10px',
                        cursor: 'pointer',
                        background: isSelected ? 'rgba(0, 242, 254, 0.12)' : 'rgba(255, 255, 255, 0.02)',
                        border: isSelected ? '2px solid var(--cyan)' : '1px solid var(--border)',
                        boxShadow: isSelected ? '0 0 15px rgba(0, 242, 254, 0.25)' : 'none',
                        transition: 'all 0.2s ease',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '6px'
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <strong style={{ fontSize: '14px', color: isSelected ? 'var(--cyan)' : 'var(--text-main)' }}>
                          {m.title}
                        </strong>
                        <span className={`tag-badge ${isSelected ? 'cyan' : 'emerald'}`} style={{ fontSize: '10px' }}>
                          {m.badge}
                        </span>
                      </div>
                      <p style={{ fontSize: '11.5px', color: 'var(--text-sub)', margin: 0, lineHeight: 1.5 }}>
                        {m.desc}
                      </p>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* 1B. Mức Chọn Số Câu Một Lô (Batch Sentences: 150 - 500 câu chuẩn AIRead) */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
                <label style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-main)', margin: 0 }}>
                  Chọn Số Câu Một Lô Dịch (Lô Lớn 150 – 500 Câu Chuẩn AIRead):
                </label>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <span style={{ fontSize: '11px', color: 'var(--text-dim)' }}>Tùy chỉnh:</span>
                  <input
                    type="number"
                    min="50"
                    max="1000"
                    step="50"
                    value={translationBatchSize}
                    onChange={(e) => setTranslationBatchSize(parseInt(e.target.value, 10) || 300)}
                    style={{
                      width: '75px',
                      padding: '4px 8px',
                      background: 'var(--bg-input)',
                      border: '1px solid var(--border)',
                      borderRadius: '4px',
                      color: 'var(--cyan)',
                      fontSize: '12px',
                      fontFamily: 'var(--font-mono)',
                      fontWeight: 800,
                      textAlign: 'center'
                    }}
                  />
                  <span style={{ fontSize: '11px', color: 'var(--text-dim)' }}>câu</span>
                </div>
              </div>

              {/* Preset Buttons Grid for Sentences */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: '10px' }}>
                {batchSentencesTiers.map((t) => {
                  const isSelected = translationBatchSize === t.value;
                  return (
                    <div
                      key={t.value}
                      onClick={() => setTranslationBatchSize(t.value)}
                      style={{
                        padding: '12px 14px',
                        borderRadius: '8px',
                        cursor: 'pointer',
                        background: isSelected ? 'rgba(0, 242, 254, 0.15)' : 'rgba(255, 255, 255, 0.02)',
                        border: isSelected ? '2px solid var(--cyan)' : '1px solid var(--border)',
                        transition: 'all 0.2s ease',
                        textAlign: 'center'
                      }}
                    >
                      <strong style={{ fontSize: '13px', color: isSelected ? 'var(--cyan)' : 'var(--text-main)', display: 'block', marginBottom: '2px' }}>
                        {t.label}
                      </strong>
                      <span style={{ fontSize: '11px', color: isSelected ? 'var(--text-main)' : 'var(--text-dim)' }}>
                        {t.sub}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>

          </div>

          {/* ========================================================================= */}
          {/* SECTION 2: TIKTOK TTS MULTI-THREADING (PRESET TIERS UP TO 128 WORKERS)   */}
          {/* ========================================================================= */}
          <div className="glass-panel" style={{
            padding: '24px',
            borderRadius: 'var(--radius-md)',
            border: '1px solid rgba(245, 158, 11, 0.3)',
            display: 'flex',
            flexDirection: 'column',
            gap: '18px'
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <div style={{ width: '32px', height: '32px', borderRadius: '8px', background: 'rgba(245, 158, 11, 0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <Volume2 size={18} color="var(--amber)" />
                </div>
                <div>
                  <h3 style={{ fontSize: '16px', fontWeight: 800, color: 'var(--text-main)', margin: 0 }}>
                    2. Cấu Hình Động Cơ Giọng Đọc AI & Đa Luồng (CapCut Cloud vs TikTok)
                  </h3>
                  <p style={{ fontSize: '12px', color: 'var(--text-sub)', margin: '2px 0 0 0' }}>
                    Chọn nền tảng tạo giọng đọc lồng tiếng và mức độ xử lý song song lên tới 128 luồng
                  </p>
                </div>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span style={{ fontSize: '12px', color: 'var(--text-sub)' }}>Đang chọn:</span>
                <span className={`tag-badge ${ttsEngine === 'capcut' ? 'cyan' : 'purple'}`} style={{ fontSize: '12px', padding: '4px 12px', fontWeight: 800 }}>
                  {ttsEngine === 'capcut' ? '⚡ CAPCUT CLOUD TTS' : '🎵 TIKTOK TTS'}
                </span>
                <span className="tag-badge amber" style={{ fontSize: '12px', padding: '4px 12px', fontWeight: 800, fontFamily: 'var(--font-mono)' }}>
                  ⚡ {ttsThreads} LUỒNG
                </span>
              </div>
            </div>

            {/* TTS Engine Selection: CapCut Cloud TTS vs TikTok TTS */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '14px' }}>
              {/* Option 1: CapCut Cloud TTS */}
              <div
                onClick={() => setTtsEngine('capcut')}
                style={{
                  padding: '16px',
                  borderRadius: '10px',
                  cursor: 'pointer',
                  background: ttsEngine === 'capcut' ? 'rgba(0, 242, 254, 0.14)' : 'rgba(255, 255, 255, 0.02)',
                  border: ttsEngine === 'capcut' ? '2px solid var(--cyan)' : '1px solid var(--border)',
                  boxShadow: ttsEngine === 'capcut' ? '0 0 20px rgba(0, 242, 254, 0.28)' : 'none',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '6px',
                  transition: 'all 0.2s ease'
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <strong style={{ color: ttsEngine === 'capcut' ? 'var(--cyan)' : 'var(--text-main)', fontSize: '15px' }}>
                    ⚡ CapCut Cloud TTS (ByteDance SAMI)
                  </strong>
                  <span className="tag-badge cyan" style={{ fontSize: '9.5px', fontWeight: 800 }}>
                    Khuyên Dùng • 280 Ký Tự
                  </span>
                </div>
                <div style={{ fontSize: '11.5px', color: 'var(--text-sub)', lineHeight: 1.45 }}>
                  Tự động xoay Device ID ngẫu nhiên để <strong>lách giới hạn Rate Limit khi chạy 128 luồng song song</strong>. Đọc câu dài 250 - 300 ký tự không bị cụt câu, tải trực tiếp MP3 từ ByteDance CDN.
                </div>
              </div>

              {/* Option 2: TikTok TTS API */}
              <div
                onClick={() => setTtsEngine('tiktok')}
                style={{
                  padding: '16px',
                  borderRadius: '10px',
                  cursor: 'pointer',
                  background: ttsEngine === 'tiktok' ? 'rgba(168, 85, 247, 0.16)' : 'rgba(255, 255, 255, 0.02)',
                  border: ttsEngine === 'tiktok' ? '2px solid var(--purple)' : '1px solid var(--border)',
                  boxShadow: ttsEngine === 'tiktok' ? '0 0 18px rgba(168, 85, 247, 0.25)' : 'none',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '6px',
                  transition: 'all 0.2s ease'
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <strong style={{ color: ttsEngine === 'tiktok' ? 'var(--purple-light)' : 'var(--text-main)', fontSize: '15px' }}>
                    🎵 TikTok TTS API (Nguyên Bản)
                  </strong>
                  <span className="tag-badge purple" style={{ fontSize: '9.5px', fontWeight: 800 }}>
                    Cần Session Cookie
                  </span>
                </div>
                <div style={{ fontSize: '11.5px', color: 'var(--text-sub)', lineHeight: 1.45 }}>
                  Gọi trực tiếp endpoint TikTok âm thanh gốc qua Cookie <code>sessionid</code>. Giới hạn khoảng 140 ký tự mỗi chunk (câu dài sẽ tự tách thành nhiều phần nhỏ).
                </div>
              </div>
            </div>

            {/* Chi tiết / Cấu hình Cookie tương ứng từng Engine */}
            {ttsEngine === 'capcut' ? (
              <div style={{
                padding: '14px 16px',
                borderRadius: '8px',
                background: 'rgba(0, 242, 254, 0.06)',
                border: '1px solid rgba(0, 242, 254, 0.22)',
                display: 'flex',
                flexDirection: 'column',
                gap: '10px'
              }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '8px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <Sparkles size={16} color="var(--cyan)" />
                    <span style={{ fontSize: '12.5px', color: 'var(--text-main)' }}>
                      Đang kích hoạt <strong>CapCut Cloud TTS Engine</strong>: Hỗ trợ trọn vẹn 9 giọng đọc tiếng Việt (Cô Gái Hoạt Ngôn, Nhỏ Ngọt Ngào, Mai, Hương, Nam Hào Sảng...). Tự động ký chữ ký số thiết bị.
                    </span>
                  </div>
                  <span className="tag-badge cyan" style={{ fontSize: '10px', fontWeight: 800 }}>
                    AUTO DEVICE ROTATION
                  </span>
                </div>
                <div>
                  <label style={{ fontSize: '11.5px', color: 'var(--text-sub)', display: 'block', marginBottom: '4px' }}>
                    CapCut Cookie / Token (Tùy chọn nâng cao - Mặc định hệ thống tự động tạo Sign không cần cookie):
                  </label>
                  <input
                    type="text"
                    placeholder="Để trống nếu không cần, hoặc dán cookie CapCut nếu có..."
                    value={capcutCookie}
                    onChange={(e) => setCapcutCookie(e.target.value)}
                    style={{
                      width: '100%',
                      padding: '8px 12px',
                      background: 'var(--bg-input)',
                      border: '1px solid var(--border)',
                      borderRadius: 'var(--radius-sm)',
                      color: 'var(--text-main)',
                      fontSize: '12px',
                      fontFamily: 'var(--font-mono)'
                    }}
                  />
                </div>
              </div>
            ) : (
              <div style={{
                padding: '14px 16px',
                borderRadius: '8px',
                background: 'rgba(168, 85, 247, 0.08)',
                border: '1px solid rgba(168, 85, 247, 0.25)',
                display: 'flex',
                flexDirection: 'column',
                gap: '10px'
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <Volume2 size={16} color="var(--purple-light)" />
                  <span style={{ fontSize: '12.5px', color: 'var(--text-main)' }}>
                    Đang chọn <strong>TikTok TTS Legacy Engine</strong>: Yêu cầu Cookie SessionID còn hạn sử dụng để gọi API.
                  </span>
                </div>
                <div>
                  <label style={{ fontSize: '11.5px', color: 'var(--text-sub)', display: 'block', marginBottom: '4px' }}>
                    TikTok Cookie Session ID (sessionid):
                  </label>
                  <input
                    type="text"
                    placeholder="Nhập sessionid TikTok (Mặc định: 410bfa37bdc185e1c6da82e1afb48409)..."
                    value={tiktokSessionId}
                    onChange={(e) => setTiktokSessionId(e.target.value)}
                    style={{
                      width: '100%',
                      padding: '8px 12px',
                      background: 'var(--bg-input)',
                      border: '1px solid var(--border)',
                      borderRadius: 'var(--radius-sm)',
                      color: 'var(--purple-light)',
                      fontSize: '12px',
                      fontFamily: 'var(--font-mono)'
                    }}
                  />
                </div>
              </div>
            )}

            {/* Mức Luồng Bấm Chọn (Clickable Tier Cards - Không Dùng Thanh Kéo) */}
            <div>
              <div style={{ fontSize: '12.5px', fontWeight: 700, color: 'var(--text-main)', marginBottom: '8px' }}>
                Mức độ phân luồng song song (Workers Concurrency):
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '12px' }}>
                {ttsThreadsTiers.map((t) => {
                  const isSelected = ttsThreads === t.value;
                  return (
                    <div
                      key={t.value}
                      onClick={() => setTtsThreads(t.value)}
                      style={{
                        padding: '14px 12px',
                        borderRadius: '10px',
                        cursor: 'pointer',
                        background: isSelected ? 'rgba(245, 158, 11, 0.18)' : 'rgba(255, 255, 255, 0.02)',
                        border: isSelected ? '2px solid var(--amber)' : '1px solid var(--border)',
                        boxShadow: isSelected ? '0 0 18px rgba(245, 158, 11, 0.3)' : 'none',
                        transition: 'all 0.2s ease',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '4px',
                        textAlign: 'center'
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '6px' }}>
                        <strong style={{ fontSize: '15px', color: isSelected ? 'var(--amber)' : 'var(--text-main)' }}>
                          {t.label}
                        </strong>
                      </div>
                      <span className={`tag-badge ${isSelected ? 'amber' : 'emerald'}`} style={{ fontSize: '10px', margin: '2px auto' }}>
                        {t.tag}
                      </span>
                      <span style={{ fontSize: '11px', color: isSelected ? 'var(--text-main)' : 'var(--text-dim)', marginTop: '2px' }}>
                        {t.desc}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'rgba(0,0,0,0.2)', padding: '10px 14px', borderRadius: '6px' }}>
              <span style={{ fontSize: '12px', color: 'var(--text-dim)' }}>
                * Với mức 64 - 128 luồng, toàn bộ lời thoại video 10-20 phút được tạo giọng đọc trong <strong>~3 đến 5 giây</strong>.
              </span>
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <span style={{ fontSize: '11px', color: 'var(--text-dim)' }}>Nhập số khác:</span>
                <input
                  type="number"
                  min="1"
                  max="128"
                  value={ttsThreads}
                  onChange={(e) => setTtsThreads(Math.max(1, Math.min(128, parseInt(e.target.value, 10) || 64)))}
                  style={{
                    width: '60px',
                    padding: '3px 6px',
                    background: 'var(--bg-input)',
                    border: '1px solid var(--border)',
                    borderRadius: '4px',
                    color: 'var(--amber)',
                    fontSize: '12px',
                    fontFamily: 'var(--font-mono)',
                    fontWeight: 800,
                    textAlign: 'center'
                  }}
                />
              </div>
            </div>
          </div>

          {/* ========================================================================= */}
          {/* SECTION 3: SPEECH-TO-TEXT ASR (GROQ WHISPER CLOUD / FASTER-WHISPER)      */}
          {/* ========================================================================= */}
          <div className="glass-panel" style={{
            padding: '24px',
            borderRadius: 'var(--radius-md)',
            border: '1px solid rgba(168, 85, 247, 0.3)',
            display: 'flex',
            flexDirection: 'column',
            gap: '18px'
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <div style={{ width: '32px', height: '32px', borderRadius: '8px', background: 'rgba(168, 85, 247, 0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <CloudLightning size={18} color="var(--purple)" />
                </div>
                <div>
                  <h3 style={{ fontSize: '16px', fontWeight: 800, color: 'var(--text-main)', margin: 0 }}>
                    3. Cấu Hình AI Bóc Tách Lời Thoại (Speech-to-Text ASR)
                  </h3>
                  <p style={{ fontSize: '12px', color: 'var(--text-sub)', margin: '2px 0 0 0' }}>
                    Chuyển giọng nói tiếng Trung thành phụ đề và timecode từng mili-giây
                  </p>
                </div>
              </div>

              <a
                href="https://console.groq.com/keys"
                target="_blank"
                rel="noreferrer"
                style={{
                  fontSize: '12px',
                  color: 'var(--purple-light)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px',
                  textDecoration: 'none',
                  fontWeight: 700,
                  background: 'rgba(168, 85, 247, 0.1)',
                  padding: '5px 10px',
                  borderRadius: '6px',
                  border: '1px solid rgba(168, 85, 247, 0.25)'
                }}
              >
                Lấy Groq API Key <ExternalLink size={12} />
              </a>
            </div>

            {/* Groq API Key Input */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '6px' }}>
                <label style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-main)' }}>
                  Groq Cloud API Key (Hiển thị trực tiếp):
                </label>
                <button
                  type="button"
                  onClick={handleTestGroq}
                  disabled={testingGroq}
                  style={{
                    background: 'transparent',
                    border: 'none',
                    color: 'var(--purple-light)',
                    fontSize: '11.5px',
                    fontWeight: 700,
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '4px'
                  }}
                >
                  {testingGroq ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
                  Kiểm tra kết nối Groq API
                </button>
              </div>

              <div style={{ position: 'relative' }}>
                <input
                  type={showGroqKey ? 'text' : 'password'}
                  placeholder="gsk_..."
                  value={groqKey}
                  onChange={(e) => setGroqKey(e.target.value)}
                  style={{
                    width: '100%',
                    padding: '12px 42px 12px 14px',
                    background: 'var(--bg-input)',
                    border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-sm)',
                    color: 'var(--text-main)',
                    fontSize: '13px',
                    fontFamily: 'var(--font-mono)'
                  }}
                />
                <button
                  type="button"
                  onClick={() => setShowGroqKey(!showGroqKey)}
                  style={{
                    position: 'absolute',
                    right: '12px',
                    top: '50%',
                    transform: 'translateY(-50%)',
                    background: 'none',
                    border: 'none',
                    color: 'var(--text-dim)',
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center'
                  }}
                  title={showGroqKey ? 'Ẩn API Key' : 'Hiện API Key'}
                >
                  {showGroqKey ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>

              {testResult && (
                <div style={{
                  marginTop: '10px',
                  padding: '8px 12px',
                  borderRadius: '6px',
                  fontSize: '12px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  background: testResult.success ? 'rgba(16, 185, 129, 0.15)' : 'rgba(244, 63, 94, 0.15)',
                  border: `1px solid ${testResult.success ? 'var(--emerald)' : 'var(--rose)'}`,
                  color: testResult.success ? 'var(--emerald)' : 'var(--rose)'
                }}>
                  {testResult.success ? <CheckCircle2 size={14} /> : <AlertCircle size={14} />}
                  {testResult.message}
                </div>
              )}
            </div>

            {/* Engine Selection: CapCut vs Groq (Card Buttons) */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '14px' }}>
              {/* Option 1: CapCut Cloud STT (Khuyến nghị số 1) */}
              <div
                onClick={() => setAsrEngine('capcut')}
                style={{
                  padding: '16px',
                  borderRadius: '10px',
                  cursor: 'pointer',
                  background: asrEngine === 'capcut' ? 'rgba(0, 242, 254, 0.16)' : 'rgba(255, 255, 255, 0.02)',
                  border: asrEngine === 'capcut' ? '2px solid var(--cyan)' : '1px solid var(--border)',
                  boxShadow: asrEngine === 'capcut' ? '0 0 18px rgba(0, 242, 254, 0.3)' : 'none',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '4px',
                  position: 'relative'
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <strong style={{ color: asrEngine === 'capcut' ? 'var(--cyan)' : 'var(--text-main)', fontSize: '14.5px' }}>
                    ⚡ CapCut Cloud STT
                  </strong>
                  <span className="tag-badge cyan" style={{ fontSize: '9px', fontWeight: 800 }}>Khuyên Dùng • 0% CPU</span>
                </div>
                <div style={{ fontSize: '11.5px', color: 'var(--text-sub)', marginTop: '2px', lineHeight: 1.4 }}>
                  Siêu bóc timecode chuẩn từng mili-giây mở/ngậm miệng nhân vật. Không cần API Key, xử lý 10 phút chỉ trong 5-10 giây.
                </div>
              </div>

              {/* Option 2: Groq Whisper Cloud */}
              <div
                onClick={() => setAsrEngine('groq')}
                style={{
                  padding: '16px',
                  borderRadius: '10px',
                  cursor: 'pointer',
                  background: asrEngine === 'groq' ? 'rgba(168, 85, 247, 0.18)' : 'rgba(255, 255, 255, 0.02)',
                  border: asrEngine === 'groq' ? '2px solid var(--purple)' : '1px solid var(--border)',
                  boxShadow: asrEngine === 'groq' ? '0 0 15px rgba(168, 85, 247, 0.25)' : 'none',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '4px'
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <strong style={{ color: asrEngine === 'groq' ? 'var(--purple-light)' : 'var(--text-main)', fontSize: '14px' }}>
                    🚀 Groq Whisper Large-v3
                  </strong>
                  <span className="tag-badge emerald" style={{ fontSize: '9px' }}>Cần API Key</span>
                </div>
                <div style={{ fontSize: '11.5px', color: 'var(--text-sub)', marginTop: '2px', lineHeight: 1.4 }}>
                  Bóc tách video qua Groq Cloud (cần nhập Groq API Key ở trên).
                </div>
              </div>
            </div>

            {/* Thông báo chi tiết khi chọn CapCut STT */}
            {asrEngine === 'capcut' && (
              <div style={{
                padding: '12px 16px',
                borderRadius: '8px',
                background: 'rgba(0, 242, 254, 0.08)',
                border: '1px solid rgba(0, 242, 254, 0.25)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                flexWrap: 'wrap',
                gap: '10px'
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <Sparkles size={16} color="var(--cyan)" />
                  <span style={{ fontSize: '12.5px', color: 'var(--text-main)' }}>
                    Đang kích hoạt <strong>CapCut Cloud STT</strong>: Bóc tách lời thoại và tính toán khung mở/ngậm miệng chuẩn xác từng mili-giây qua máy chủ ByteDance Cloud. Miễn phí 100%, không tốn CPU.
                  </span>
                </div>
                <span className="tag-badge cyan" style={{ fontSize: '11px', fontWeight: 800 }}>
                  CHUẨN KHẨU HÌNH LỒNG TIẾNG
                </span>
              </div>
            )}
          </div>

          {/* Bottom Save Bar */}
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '12px', marginTop: '10px' }}>
            <button
              type="submit"
              disabled={saving}
              className="btn btn-primary"
              style={{
                padding: '14px 36px',
                fontSize: '15px',
                fontWeight: 800,
                background: 'var(--grad-cyan)',
                color: '#070913',
                boxShadow: '0 0 25px rgba(0, 242, 254, 0.4)',
                display: 'flex',
                alignItems: 'center',
                gap: '8px'
              }}
            >
              {saving ? <Loader2 size={18} className="animate-spin" /> : <Save size={18} />}
              LƯU TOÀN BỘ CÀI ĐẶT HỆ THỐNG
            </button>
          </div>

        </form>
      )}

      {/* Storage Architecture Overview */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>
        <div className="glass-panel" style={{ padding: '20px', borderRadius: 'var(--radius-md)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
            <FolderDown size={18} color="var(--cyan)" />
            <h4 style={{ fontSize: '14px', fontWeight: 800, color: 'var(--text-main)', margin: 0 }}>
              Cấu Trúc Thư Mục Lưu Trữ
            </h4>
          </div>
          <div style={{ fontSize: '12px', color: 'var(--text-sub)', lineHeight: 1.6 }}>
            • <code>input/videos/</code>: Video MP4 gốc tải về.<br />
            • <code>input/audio_raw/</code>: Audio 16kHz WAV phục vụ AI ASR.<br />
            • <code>output/transcripts/</code>: File <code>.srt</code>, <code>.ass</code> Karaoke.<br />
            • <code>output/voiceover/</code>: Audio TikTok TTS lồng tiếng.<br />
            • <code>output/final_videos/</code>: Video hoàn chỉnh chuẩn FastStart.
          </div>
        </div>

        <div className="glass-panel" style={{ padding: '20px', borderRadius: 'var(--radius-md)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
            <ShieldCheck size={18} color="var(--emerald)" />
            <h4 style={{ fontSize: '14px', fontWeight: 800, color: 'var(--text-main)', margin: 0 }}>
              Tối Ưu Hóa & Bộ Đệm RAM
            </h4>
          </div>
          <div style={{ fontSize: '12px', color: 'var(--text-sub)', lineHeight: 1.6 }}>
            • <strong>Stream Copy 0.5s:</strong> Ghép video và âm thanh tức thì không encode lại.<br />
            • <strong>HTTP Range 206:</strong> Xem video trên web không tràn RAM hay giật lag.<br />
            • <strong>CUDA + Intel GPU:</strong> Phối hợp 2 GPU xử lý bóc tách và xuất video.
          </div>
        </div>
      </div>

    </div>
  );
}
