import React, { useState, useEffect } from 'react';
import TopNavbar from './components/TopNavbar';
import MainStudioView from './components/MainStudioView';
import DialogueLibraryView from './components/DialogueLibraryView';
import EntitiesDictionaryView from './components/EntitiesDictionaryView';
import VideoGalleryView from './components/VideoGalleryView';
import SettingsTab from './components/SettingsTab';

export default function App() {
  const getTabFromHash = () => {
    const hash = window.location.hash.replace('#/', '').replace('#', '').trim();
    const validTabs = ['studio', 'dialogues', 'entities', 'gallery', 'settings'];
    return validTabs.includes(hash) ? hash : 'studio';
  };

  const [activeTab, setActiveTabState] = useState(getTabFromHash);
  const [totalVideos, setTotalVideos] = useState(0);
  const [totalDialogues, setTotalDialogues] = useState(0);

  const setActiveTab = (tab) => {
    window.location.hash = `#/${tab}`;
    setActiveTabState(tab);
  };

  useEffect(() => {
    const handleHashChange = () => {
      const current = getTabFromHash();
      setActiveTabState(current);
    };
    window.addEventListener('hashchange', handleHashChange);
    if (!window.location.hash) {
      window.location.hash = `#/studio`;
    }
    return () => window.removeEventListener('hashchange', handleHashChange);
  }, []);

  const fetchGlobalStats = async () => {
    try {
      const res = await fetch(`/api/v1/projects/?page=1&page_size=1&_t=${Date.now()}`, { cache: 'no-store' });
      if (res.ok) {
        const data = await res.json();
        setTotalVideos(data.total_items || 0);
      }
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    fetchGlobalStats();
    const handleUpdated = () => fetchGlobalStats();
    window.addEventListener('video-projects-updated', handleUpdated);
    return () => window.removeEventListener('video-projects-updated', handleUpdated);
  }, [activeTab]);

  const handleSelectVideoForStudio = (projectId) => {
    setActiveTab('studio');
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', width: '100vw', height: '100vh', overflow: 'hidden', background: 'var(--bg-dark)' }}>
      {/* 1. Horizontal Top Navigation Bar */}
      <TopNavbar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        totalVideos={totalVideos}
        totalDialogues={totalDialogues}
        onRefresh={fetchGlobalStats}
      />

      {/* 2. Full Workspace Viewport */}
      <main style={{ flex: 1, height: 'calc(100vh - 64px)', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        {/* Keep-Alive Tabs */}
        <div style={{ flex: 1, height: '100%', display: activeTab === 'studio' ? 'flex' : 'none', flexDirection: 'column', minHeight: 0 }}>
          <MainStudioView onNavigateTab={setActiveTab} />
        </div>

        <div style={{ flex: 1, height: '100%', display: activeTab === 'dialogues' ? 'flex' : 'none', flexDirection: 'column', overflowY: 'auto' }}>
          <DialogueLibraryView />
        </div>

        <div style={{ flex: 1, height: '100%', display: activeTab === 'entities' ? 'flex' : 'none', flexDirection: 'column', overflowY: 'auto' }}>
          <EntitiesDictionaryView />
        </div>

        <div style={{ flex: 1, height: '100%', display: activeTab === 'gallery' ? 'flex' : 'none', flexDirection: 'column', overflowY: 'auto' }}>
          <VideoGalleryView 
            isActive={activeTab === 'gallery'} 
            onSelectVideoForStudio={handleSelectVideoForStudio} 
          />
        </div>

        <div style={{ flex: 1, height: '100%', display: activeTab === 'settings' ? 'flex' : 'none', flexDirection: 'column', overflowY: 'auto' }}>
          <SettingsTab />
        </div>
      </main>
    </div>
  );
}
