import { useState, useEffect, useRef } from 'react';
import clsx from 'clsx';
import { AgentTrace, getMessageDelta } from '../utils/traceDetection';
import { TraceModal } from './TraceModal';
import { MessageContent } from './MessageContent';
import { SystemPromptModal } from './SystemPromptModal';

interface TraceViewProps {
  trace: AgentTrace;
  isPinned?: boolean;
  onPinToggle?: () => void;
}

export function TraceView({ trace, isPinned = false, onPinToggle }: TraceViewProps) {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isSystemPromptOpen, setIsSystemPromptOpen] = useState(false);

  // Track which turns are new for animation
  const [newTurns, setNewTurns] = useState<Set<string>>(new Set());
  const previousEventCount = useRef(trace.events.length);

  useEffect(() => {
    // Detect new events and mark them for animation
    if (trace.events.length > previousEventCount.current) {
      const newEventIds = new Set<string>();
      const newEventsCount = trace.events.length - previousEventCount.current;

      // Mark the newest events as new (they're at the end of the array)
      for (let i = trace.events.length - newEventsCount; i < trace.events.length; i++) {
        newEventIds.add(trace.events[i].id);
      }

      setNewTurns(newEventIds);

      // Remove animation class after animation completes
      setTimeout(() => {
        setNewTurns(new Set());
      }, 2000);
    }

    previousEventCount.current = trace.events.length;
  }, [trace.events.length]);

  // Get model from first event
  const model = trace.events[0]?.request.model || '';
  const modelType = model.includes('haiku') ? 'haiku' :
                    model.includes('sonnet') ? 'sonnet' :
                    model.includes('opus') ? 'opus' : 'unknown';

  // Determine visual style based on model
  const traceStyle = {
    haiku: {
      borderColor: 'border-orange-300',
      accentColor: 'text-orange-500',
      badgeBg: 'bg-white',
      badgeText: 'text-orange-500',
      dotColor: 'bg-orange-400'
    },
    sonnet: {
      borderColor: 'border-violet-300',
      accentColor: 'text-violet-500',
      badgeBg: 'bg-white',
      badgeText: 'text-violet-500',
      dotColor: 'bg-violet-400'
    },
    opus: {
      borderColor: 'border-blue-300',
      accentColor: 'text-blue-500',
      badgeBg: 'bg-white',
      badgeText: 'text-blue-500',
      dotColor: 'bg-blue-400'
    },
    unknown: {
      borderColor: 'border-gray-400',
      accentColor: 'text-gray-600',
      badgeBg: 'bg-white',
      badgeText: 'text-gray-600',
      dotColor: 'bg-gray-500'
    }
  }[modelType];


  return (
    <>
    <div className={clsx(
      "trace-view bg-white rounded-xl border-2 h-full overflow-hidden flex flex-col shadow-sm hover:shadow-lg transition-shadow",
      traceStyle.borderColor
    )}>
      {/* Trace Header */}
      <div className="trace-header px-5 py-3 bg-gradient-to-r from-gray-50 to-white border-b border-gray-200">
        <div className="flex justify-between items-center">
          <div className="flex items-center gap-3">
            {onPinToggle && (
              <button
                onClick={onPinToggle}
                className={clsx(
                  "p-1 rounded transition-colors",
                  isPinned
                    ? "text-gray-900 bg-gray-200 hover:bg-gray-300"
                    : "text-gray-400 hover:text-gray-700 hover:bg-gray-100"
                )}
                title={isPinned ? "Unpin trace" : "Pin trace"}
              >
                <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                  <path d="M10 2a1 1 0 011 1v1.323l3.879 3.879A2.25 2.25 0 0116.5 10.45V11.5a.75.75 0 01-.216.529l-7.5 7.5a.75.75 0 11-1.06-1.06l7.284-7.284A.75.75 0 0015 10.94v-.69a.75.75 0 00-.22-.53L11 5.94V15a1 1 0 11-2 0V5.94L5.22 9.72a.75.75 0 00-.22.53v.69a.75.75 0 00.008.245l7.284 7.284a.75.75 0 11-1.06 1.06l-7.5-7.5A.75.75 0 013.5 11.5v-1.05a2.25 2.25 0 011.621-2.248L9 4.323V3a1 1 0 011-1z" />
                </svg>
              </button>
            )}
            <div className={clsx('w-2.5 h-2.5 rounded-full', traceStyle.dotColor)} />
            <span className="text-base font-bold text-gray-900">
              Trace {trace.id.split('-')[1]}
            </span>
            <span className={clsx(
              'px-2.5 py-1 text-xs font-semibold rounded border',
              traceStyle.badgeBg,
              traceStyle.badgeText,
              modelType === 'haiku' ? 'border-orange-200' :
              modelType === 'sonnet' ? 'border-violet-200' :
              modelType === 'opus' ? 'border-blue-200' :
              'border-gray-200'
            )}>
              {modelType.toUpperCase()}
            </span>
          </div>
          <button
            onClick={() => setIsModalOpen(true)}
            className="text-gray-600 hover:text-gray-900 p-1 hover:bg-gray-100 rounded"
            title="View details"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
            </svg>
          </button>
        </div>
      </div>

      {/* Events in Trace (reversed to show newest first) */}
      <div className="flex-1 overflow-y-auto p-5 bg-gradient-to-b from-white via-gray-50/20 to-gray-50/40">
        <div className="space-y-3">
        {trace.events.slice().reverse().map((event, reversedIndex) => {
          const originalIndex = trace.events.length - 1 - reversedIndex;
          const prevEvent = originalIndex > 0 ? trace.events[originalIndex - 1] : null;
          const delta = getMessageDelta(prevEvent, event);

          // Build tool ID to name mapping from previous response
          const toolIdToName: Record<string, string> = {};
          if (prevEvent?.response?.content) {
            prevEvent.response.content.forEach((block: any) => {
              if (block.type === 'tool_use' && block.id && block.name) {
                toolIdToName[block.id] = block.name;
              }
            });
          }

          return (
            <div key={event.id} className={clsx(
              "event-in-trace bg-white rounded-xl border-2 border-gray-300 shadow-sm",
              newTurns.has(event.id) && "animate-new-turn"
            )}>
              {/* Turn Header */}
              <div className="px-3 py-2 bg-gradient-to-r from-gray-100 to-gray-50 border-b border-gray-200 rounded-t-lg">
                <span className="text-xs font-bold text-gray-700 uppercase tracking-wider">Turn {originalIndex + 1}</span>
              </div>
              <div className="p-4 bg-gradient-to-b from-white to-gray-50/30">

              {/* Show only truly new user messages (not assistant responses from history) */}
              {(() => {
                // Only show user messages and tool results, skip assistant messages from history
                const newUserMessages = delta.addedMessages.filter(msg => msg.role !== 'assistant');

                if (newUserMessages.length === 0) return null;

                return (
                  <div className="relative pl-4 py-3 mb-5 border-l-2 border-gray-400">
                    <div className="text-xs font-bold text-gray-700 uppercase tracking-wider mb-2">
                      User
                    </div>
                    {newUserMessages.map((msg, i) => (
                      <div key={i} className="mb-2">
                        <MessageContent content={msg.content} toolIdToName={toolIdToName} />
                      </div>
                    ))}
                  </div>
                );
              })()}

              {/* Show response */}
              {event.response?.content && (
                <div className={clsx(
                  "relative pl-4 py-3 border-l-2",
                  modelType === 'haiku' ? 'border-orange-400' :
                  modelType === 'sonnet' ? 'border-violet-400' :
                  modelType === 'opus' ? 'border-blue-400' :
                  'border-gray-400'
                )}>
                  <div className="text-xs font-bold text-gray-700 uppercase tracking-wider mb-2">Assistant</div>
                  <MessageContent content={event.response.content} />
                </div>
              )}
              </div>
            </div>
          );
        })}

        {/* System Prompt Section */}
        {trace.events[0]?.request?.system && (
          <div className="mt-4 border-t-2 border-gray-300 pt-4">
            <div className="bg-gray-100 rounded-lg p-3 border border-gray-300">
              <div className="flex justify-between items-start">
                <div>
                  <div className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-2">System Prompt</div>
                  <div className="text-sm text-gray-600 line-clamp-2">
                    {typeof trace.events[0].request.system === 'string'
                      ? trace.events[0].request.system
                      : Array.isArray(trace.events[0].request.system)
                        ? trace.events[0].request.system[0]?.text || 'Complex system prompt...'
                        : 'Complex system prompt...'}
                  </div>
                </div>
                <button
                  onClick={() => setIsSystemPromptOpen(true)}
                  className="text-xs px-3 py-1.5 bg-white border border-gray-300 rounded-md hover:bg-gray-100 hover:border-gray-400 text-gray-700 font-medium ml-4 flex-shrink-0 transition-colors"
                >
                  View Full
                </button>
              </div>
            </div>
          </div>
        )}
        </div>
      </div>

    </div>

    {/* Full Screen Modal */}
    <TraceModal
      trace={trace}
      isOpen={isModalOpen}
      onClose={() => setIsModalOpen(false)}
      onOpenSystemPrompt={() => setIsSystemPromptOpen(true)}
    />

    {/* System Prompt Modal */}
    <SystemPromptModal
      systemPrompt={trace.events[0]?.request?.system}
      isOpen={isSystemPromptOpen}
      onClose={() => setIsSystemPromptOpen(false)}
    />
    </>
  );
}
