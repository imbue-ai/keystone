import { useState } from 'react';
import clsx from 'clsx';
import { format } from 'date-fns';
import JsonView from '@uiw/react-json-view';
import { lightTheme } from '@uiw/react-json-view/light';
import { AgentTrace, getMessageDelta } from '../utils/traceDetection';
import { MessageContent } from './MessageContent';
import { SystemPromptModal } from './SystemPromptModal';

interface TraceModalProps {
  trace: AgentTrace;
  isOpen: boolean;
  onClose: () => void;
  onOpenSystemPrompt?: () => void;
}

export function TraceModal({ trace, isOpen, onClose, onOpenSystemPrompt }: TraceModalProps) {
  const [selectedEventIndex, setSelectedEventIndex] = useState(trace.events.length - 1);
  const [isSystemPromptOpen, setIsSystemPromptOpen] = useState(false);

  if (!isOpen) return null;

  const model = trace.events[0]?.request.model || '';
  const modelType = model.includes('haiku') ? 'haiku' :
                    model.includes('sonnet') ? 'sonnet' :
                    model.includes('opus') ? 'opus' : 'unknown';

  const selectedEvent = trace.events[selectedEventIndex];

  return (
    <>
    <div className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm flex items-center justify-center p-8" onClick={onClose}>
      <div className="bg-white w-full h-full max-w-[90vw] max-h-[85vh] rounded-2xl shadow-modal flex flex-col overflow-hidden" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="bg-white border-b-2 border-gray-300 px-8 py-5 flex-shrink-0">
          <div className="flex justify-between items-center">
            <div className="flex items-center gap-4">
              <h2 className="text-xl font-bold text-gray-900">
                Trace {trace.id.split('-')[1]} Details
              </h2>
              <span className={clsx(
                'px-3 py-1.5 text-xs font-bold rounded border',
                modelType === 'haiku' ? 'bg-orange-50 text-orange-600 border-orange-200' :
                modelType === 'sonnet' ? 'bg-violet-50 text-violet-600 border-violet-200' :
                modelType === 'opus' ? 'bg-blue-50 text-blue-600 border-blue-200' :
                'bg-gray-50 text-gray-600 border-gray-200'
              )}>
                {modelType.toUpperCase()}
              </span>
              <span className="text-sm text-gray-600 font-medium">
                <span className="font-bold text-gray-900">{trace.events.length}</span> turns • {format(new Date(trace.startTime), 'h:mm:ss a')}
              </span>
            </div>
            <button
              onClick={onClose}
              className="text-gray-400 hover:text-gray-600 p-1.5 hover:bg-gray-100 rounded-lg"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        {/* Main Content */}
        <div className="flex-1 flex min-h-0">
        {/* Left Side - Messages */}
        <div className="w-1/2 border-r-2 border-gray-200 flex flex-col bg-gradient-to-b from-gray-50 to-white">
          <div className="bg-gradient-to-r from-white to-gray-50 px-6 py-3 border-b border-gray-200">
            <h3 className="text-xs font-bold text-gray-500 uppercase tracking-wider">Conversation</h3>
          </div>
          <div className="flex-1 overflow-y-auto p-6 bg-gradient-to-b from-gray-50/50 to-white">
            <div className="space-y-4">
              {trace.events.slice().reverse().map((event, reversedIndex) => {
                const index = trace.events.length - 1 - reversedIndex;
                const eventPrev = index > 0 ? trace.events[index - 1] : null;
                const eventDelta = getMessageDelta(eventPrev, event);
                const isSelected = index === selectedEventIndex;

                return (
                  <div
                    key={event.id}
                    className={clsx(
                      "rounded-xl border-2 cursor-pointer bg-white transition-all",
                      isSelected
                        ? "border-gray-600 shadow-lg"
                        : "border-gray-300 hover:border-gray-400 hover:shadow-md"
                    )}
                    onClick={() => setSelectedEventIndex(index)}
                  >
                    {/* Turn Header */}
                    <div className={clsx(
                      "px-4 py-2 border-b rounded-t-xl",
                      isSelected ? "bg-gray-200 border-gray-300" : "bg-gray-100 border-gray-200"
                    )}>
                      <span className="text-xs font-bold text-gray-700 uppercase tracking-wider">Turn {index + 1}</span>
                    </div>
                    <div className="p-4">


                      {/* Build tool ID mapping from previous response */}
                      {(() => {
                        const toolIdToName: Record<string, string> = {};
                        if (eventPrev?.response?.content) {
                          eventPrev.response.content.forEach((block: any) => {
                            if (block.type === 'tool_use' && block.id && block.name) {
                              toolIdToName[block.id] = block.name;
                            }
                          });
                        }

                        const newUserMessages = eventDelta.addedMessages.filter(msg => msg.role !== 'assistant');

                        return (
                          <>
                            {/* User Input */}
                            {newUserMessages.length > 0 && (
                              <div className="relative pl-4 py-3 mb-5 border-l-2 border-gray-400">
                                <div className="text-xs font-bold text-gray-700 uppercase tracking-wider mb-2">User</div>
                                {newUserMessages.map((msg, i) => (
                                  <div key={i}>
                                    <MessageContent content={msg.content} toolIdToName={toolIdToName} />
                                  </div>
                                ))}
                              </div>
                            )}

                            {/* Assistant Response */}
                            {event.response?.content && (
                              <div className={clsx(
                                "relative pl-4 py-3 border-l-2",
                                modelType === 'haiku' ? 'border-orange-400' :
                                modelType === 'sonnet' ? 'border-violet-400' :
                                modelType === 'opus' ? 'border-blue-400' :
                                'border-gray-400'
                              )}>
                                <div className="text-xs font-bold text-gray-700 uppercase tracking-wider mb-2">Assistant</div>
                                <div>
                                  <MessageContent content={event.response.content} />
                                </div>
                              </div>
                            )}
                          </>
                        );
                      })()}
                    </div>
                  </div>
                );
              })}

              {/* System Prompt Section at bottom */}
              {trace.events[0]?.request?.system && (
                <div className="mt-4 border-t-2 border-gray-300 pt-4">
                  <div className="bg-gray-100 rounded-lg p-3 border border-gray-300">
                    <div className="flex justify-between items-start">
                      <div className="flex-1">
                        <div className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-2">System Prompt</div>
                        <div className="text-sm text-gray-600 line-clamp-3">
                          {typeof trace.events[0].request.system === 'string'
                            ? trace.events[0].request.system
                            : Array.isArray(trace.events[0].request.system)
                              ? trace.events[0].request.system[0]?.text || 'Complex system prompt...'
                              : 'Complex system prompt...'}
                        </div>
                      </div>
                      <button
                        onClick={() => onOpenSystemPrompt ? onOpenSystemPrompt() : setIsSystemPromptOpen(true)}
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

        {/* Right Side - API Details */}
        <div className="w-1/2 flex flex-col bg-gradient-to-b from-white to-gray-50/30">
          <div className="bg-gradient-to-r from-gray-50 to-white px-6 py-3 border-b border-gray-200">
            <h3 className="text-xs font-bold text-gray-500 uppercase tracking-wider">
              API Details {selectedEvent && <span className="text-gray-600">• Turn {selectedEventIndex + 1}</span>}
            </h3>
          </div>
          <div className="flex-1 overflow-y-auto p-6 bg-gradient-to-b from-white to-gray-50/20">
            {selectedEvent ? (
              <div className="space-y-6">
                {/* API Request */}
                <div className="bg-gray-50 rounded-xl p-5 border border-gray-400">
                  <div className="flex justify-between items-center mb-4">
                    <h4 className="text-sm font-semibold text-gray-900">
                      Request
                    </h4>
                    <button
                      onClick={() => navigator.clipboard.writeText(JSON.stringify(selectedEvent.request, null, 2))}
                      className="text-xs px-3 py-1.5 bg-white border border-gray-400 rounded-md hover:bg-gray-100 hover:border-gray-500 text-gray-700 hover:text-gray-900 font-medium transition-colors"
                    >
                      Copy JSON
                    </button>
                  </div>

                  <div className="overflow-x-auto bg-white p-3 rounded-lg border border-gray-400">
                    <JsonView
                      value={selectedEvent.request}
                      style={lightTheme}
                      collapsed={2}
                    />
                  </div>
                </div>

                {/* API Response */}
                <div className="bg-gray-50 rounded-xl p-5 border border-gray-400">
                  <div className="flex justify-between items-center mb-4">
                    <h4 className="text-sm font-semibold text-gray-900">Response</h4>
                    <button
                      onClick={() => navigator.clipboard.writeText(JSON.stringify(selectedEvent.response, null, 2))}
                      className="text-xs px-3 py-1.5 bg-white border border-gray-400 rounded-md hover:bg-gray-100 hover:border-gray-500 text-gray-700 hover:text-gray-900 font-medium transition-colors"
                    >
                      Copy JSON
                    </button>
                  </div>

                  <div className="overflow-x-auto bg-white p-3 rounded-lg border border-gray-400">
                    <JsonView
                      value={selectedEvent.response}
                      style={lightTheme}
                      collapsed={2}
                    />
                  </div>
                </div>
              </div>
            ) : (
              <div className="text-center text-gray-400 mt-12">
                <svg className="w-8 h-8 mx-auto mb-3 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                    d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
                </svg>
                <div className="text-sm">Select a turn to view API details</div>
              </div>
            )}
          </div>
        </div>
      </div>
      </div>
    </div>

    {/* System Prompt Modal (stacked) */}
    {!onOpenSystemPrompt && (
      <SystemPromptModal
        systemPrompt={trace.events[0]?.request?.system}
        isOpen={isSystemPromptOpen}
        onClose={() => setIsSystemPromptOpen(false)}
      />
    )}
    </>
  );
}
