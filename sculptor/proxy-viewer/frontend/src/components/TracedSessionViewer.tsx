import { useMemo, useState, useEffect, useRef } from 'react';
import clsx from 'clsx';
import { ProxyEvent } from '../types';
import { detectTraces } from '../utils/traceDetection';
import { TraceView } from './TraceView';

interface TracedSessionViewerProps {
  events: ProxyEvent[];
}

export function TracedSessionViewer({ events }: TracedSessionViewerProps) {
  const traced = useMemo(() => detectTraces(events), [events]);

  // Model filters state
  const [filters, setFilters] = useState({
    haiku: true,
    sonnet: true,
    opus: true
  });

  // Pinned trace state
  const [pinnedTraceId, setPinnedTraceId] = useState<string | null>(null);

  // Track new traces for animation
  const [newTraces, setNewTraces] = useState<Set<string>>(new Set());
  const previousTraceCount = useRef(traced.traces.length);

  useEffect(() => {
    // Detect new traces and mark them for animation
    if (traced.traces.length > previousTraceCount.current) {
      const newTraceIds = new Set<string>();
      const newTracesCount = traced.traces.length - previousTraceCount.current;

      // Mark the newest traces as new
      for (let i = traced.traces.length - newTracesCount; i < traced.traces.length; i++) {
        newTraceIds.add(traced.traces[i].id);
      }

      setNewTraces(newTraceIds);

      // Remove animation class after animation completes
      setTimeout(() => {
        setNewTraces(new Set());
      }, 500);
    }

    previousTraceCount.current = traced.traces.length;
  }, [traced.traces.length]);

  if (events.length === 0) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <div className="text-gray-400 mb-3">
            <svg className="w-12 h-12 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
          </div>
          <h2 className="text-base font-medium text-gray-900 mb-1">
            No traces yet
          </h2>
          <p className="text-sm text-gray-500">
            API calls will appear here as you use Claude Code
          </p>
        </div>
      </div>
    );
  }

  // Filter traces based on model
  const filteredTraces = useMemo(() => {
    return traced.traces.filter(trace => {
      const model = trace.events[0]?.request.model || '';
      if (model.includes('haiku')) return filters.haiku;
      if (model.includes('sonnet')) return filters.sonnet;
      if (model.includes('opus')) return filters.opus;
      return true; // Show unknown models by default
    });
  }, [traced.traces, filters]);

  // Sort traces with newest first, and pinned trace at the very beginning
  const sortedTraces = useMemo(() => {
    // Reverse the filtered traces to show newest first
    const reversedTraces = [...filteredTraces].reverse();

    if (!pinnedTraceId) return reversedTraces;

    const pinnedTrace = reversedTraces.find(t => t.id === pinnedTraceId);
    if (!pinnedTrace) return reversedTraces;

    const otherTraces = reversedTraces.filter(t => t.id !== pinnedTraceId);
    return [pinnedTrace, ...otherTraces];
  }, [filteredTraces, pinnedTraceId]);

  // Calculate statistics
  const totalAPICalls = traced.traces.reduce((sum, trace) => sum + trace.events.length, 0);
  const totalTraces = traced.traces.length;
  const visibleTraces = filteredTraces.length;

  return (
    <div className="traced-session-viewer flex flex-col h-full">
      {/* Model Filters */}
      <div className="bg-gradient-to-r from-white to-gray-50 rounded-xl p-4 mb-6 border-2 border-gray-200 shadow-sm">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-6">
            <span className="text-xs font-bold text-gray-400 uppercase tracking-wider">Filter</span>

            <label className="flex items-center gap-2 cursor-pointer group">
              <input
                type="checkbox"
                checked={filters.haiku}
                onChange={(e) => setFilters(prev => ({ ...prev, haiku: e.target.checked }))}
                className="w-3.5 h-3.5 rounded border-gray-300 text-orange-500 focus:ring-orange-500 focus:ring-offset-0"
              />
              <span className="text-sm font-semibold text-gray-700 group-hover:text-orange-500">Haiku</span>
            </label>

            <label className="flex items-center gap-2 cursor-pointer group">
              <input
                type="checkbox"
                checked={filters.sonnet}
                onChange={(e) => setFilters(prev => ({ ...prev, sonnet: e.target.checked }))}
                className="w-3.5 h-3.5 rounded border-gray-300 text-violet-500 focus:ring-violet-500 focus:ring-offset-0"
              />
              <span className="text-sm font-semibold text-gray-700 group-hover:text-violet-500">Sonnet</span>
            </label>

            <label className="flex items-center gap-2 cursor-pointer group">
              <input
                type="checkbox"
                checked={filters.opus}
                onChange={(e) => setFilters(prev => ({ ...prev, opus: e.target.checked }))}
                className="w-3.5 h-3.5 rounded border-gray-300 text-blue-500 focus:ring-blue-500 focus:ring-offset-0"
              />
              <span className="text-sm font-semibold text-gray-700 group-hover:text-blue-500">Opus</span>
            </label>
          </div>

          <div className="flex items-center gap-6">
            <div className="text-right">
              <div className="text-2xl font-bold text-gray-900">{totalAPICalls}</div>
              <div className="text-xs text-gray-500 uppercase tracking-wide">API calls</div>
            </div>
            <div className="text-right">
              <div className="text-2xl font-bold text-gray-900">{visibleTraces}<span className="text-sm font-normal text-gray-400">/{totalTraces}</span></div>
              <div className="text-xs text-gray-500 uppercase tracking-wide">traces</div>
            </div>
          </div>
        </div>
      </div>

      {/* Render filtered traces horizontally */}
      <div className="overflow-x-auto pb-4 flex-1 bg-gradient-to-b from-white via-gray-50/10 to-gray-50/20 -mx-8 px-8">
        <div className="flex gap-5 h-full" style={{ minWidth: 'max-content' }}>
          {sortedTraces.map(trace => (
            <div key={trace.id} className={clsx(
              "flex-shrink-0 h-full",
              newTraces.has(trace.id) && "animate-new-trace"
            )} style={{ width: '420px' }}>
              <TraceView
                trace={trace}
                isPinned={trace.id === pinnedTraceId}
                onPinToggle={() => setPinnedTraceId(trace.id === pinnedTraceId ? null : trace.id)}
              />
            </div>
          ))}
        </div>
      </div>

    </div>
  );
}
