import { ProxyEvent } from '../types';
import { TracedSessionViewer } from './TracedSessionViewer';

interface SessionViewerProps {
  events: ProxyEvent[];
}

export function SessionViewer({ events }: SessionViewerProps) {

  if (events.length === 0) {
    // Get the current host from the browser
    const currentHost = window.location.host;
    const baseUrl = `http://${currentHost}`;

    return (
      <div className="flex items-start justify-center h-full pt-20">
        <div className="text-center max-w-2xl mx-auto px-8">
          <div className="text-gray-400 mb-6">
            <svg className="w-16 h-16 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
            </svg>
          </div>
          <h2 className="text-2xl font-bold text-gray-900 mb-3">
            No API calls captured yet
          </h2>
          <p className="text-base text-gray-600 mb-8">
            To start seeing Claude Code's API interactions, run Claude Code with this proxy:
          </p>

          <div className="bg-gray-900 rounded-xl p-6 shadow-lg border-2 border-gray-300">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-bold text-gray-400 uppercase tracking-wider">Run this command:</span>
              <button
                onClick={() => navigator.clipboard.writeText(`ANTHROPIC_BASE_URL=${baseUrl} claude`)}
                className="text-xs px-3 py-1.5 bg-gray-700 hover:bg-gray-600 border border-gray-600 rounded-md text-gray-300 hover:text-white font-medium transition-colors"
              >
                Copy
              </button>
            </div>
            <code className="block text-green-400 font-mono text-lg">
              <span className="text-blue-400">ANTHROPIC_BASE_URL</span>
              <span className="text-gray-500">=</span>
              <span className="text-yellow-400">{baseUrl}</span>
              <span className="text-white"> claude</span>
            </code>
          </div>

          <div className="mt-8 text-sm text-gray-500">
            <p className="mb-2">This will route Claude Code's API calls through the proxy viewer.</p>
            <p>Once you start using Claude Code, you'll see the API interactions appear here in real-time.</p>
          </div>
        </div>
      </div>
    );
  }

  // Show trace view directly
  return (
    <div className="flex flex-col h-full">
      <div className="flex justify-between items-center mb-6">
        <h2 className="text-lg font-semibold text-white">Agent Traces</h2>
      </div>
      <div className="flex-1 min-h-0">
        <TracedSessionViewer events={events} />
      </div>
    </div>
  );
}
