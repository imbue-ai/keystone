import { useEffect, useState } from 'react';
import { useWebSocket } from './hooks/useWebSocket';
import { ConnectionStatus } from './components/ConnectionStatus';
import { SessionViewer } from './components/SessionViewer';
import './styles/scrollbar.css';

function App() {
  const [wsUrl, setWsUrl] = useState('');

  useEffect(() => {
    // Construct WebSocket URL based on current location
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.hostname;
    const port = window.location.port || '8082';
    setWsUrl(`${protocol}//${host}:${port}/ws`);
  }, []);

  const { events, status } = useWebSocket(wsUrl);

  return (
    <div className="h-screen bg-gradient-to-b from-white to-gray-50/50 flex flex-col">
      {/* Header */}
      <header className="bg-white border-b-2 border-gray-200 px-8 py-5 flex-shrink-0">
        <div className="max-w-[1400px] mx-auto flex justify-between items-center">
          <div>
            <h1 className="text-xl font-bold text-gray-900">
              Claude Code Trace Viewer
            </h1>
          </div>
          <ConnectionStatus status={status} />
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-[1400px] mx-auto px-8 py-6 flex-1 min-h-0 w-full">
        <SessionViewer events={events} />
      </main>
    </div>
  );
}

export default App;
