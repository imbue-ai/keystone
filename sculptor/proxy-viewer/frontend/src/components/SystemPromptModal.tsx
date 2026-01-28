import React from 'react';
import clsx from 'clsx';

interface SystemPromptModalProps {
  systemPrompt: any;
  isOpen: boolean;
  onClose: () => void;
}

export function SystemPromptModal({ systemPrompt, isOpen, onClose }: SystemPromptModalProps) {
  if (!isOpen || !systemPrompt) return null;

  // Handle both string and array formats
  const promptContent = typeof systemPrompt === 'string'
    ? [{ type: 'text', text: systemPrompt }]
    : Array.isArray(systemPrompt)
      ? systemPrompt
      : systemPrompt.text
        ? [{ type: 'text', text: systemPrompt.text }]
        : [];

  return (
    <div className="fixed inset-0 z-[60] bg-black/40 backdrop-blur-sm flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-modal flex flex-col overflow-hidden" style={{ width: '50vw', height: '80vh' }} onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="bg-white border-b-2 border-gray-300 px-6 py-4 flex-shrink-0">
          <div className="flex justify-between items-center">
            <h2 className="text-lg font-bold text-gray-900">
              System Prompt
            </h2>
            <button
              onClick={onClose}
              className="text-gray-600 hover:text-gray-900 p-1.5 hover:bg-gray-100 rounded-lg"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6 bg-gradient-to-b from-white to-gray-50/30">
          <div className="space-y-3">
            {promptContent.map((block: any, i: number) => (
              <React.Fragment key={i}>
                {i > 0 && <div className="border-t-2 border-gray-200 my-3" />}
                <div className={clsx(
                  "text-sm rounded-lg",
                  block.type === 'text' && "p-0"
                )}>
                  {block.type === 'text' && (
                    <div className="text-gray-800 whitespace-pre-wrap font-sans leading-relaxed overflow-auto" style={{ maxHeight: 'none' }}>
                      {block.text}
                    </div>
                  )}
                  {/* Handle other content types if needed */}
                  {block.type !== 'text' && (
                    <pre className="text-gray-600 whitespace-pre-wrap font-mono p-3 bg-gray-50 rounded-lg overflow-auto" style={{ maxHeight: 'none' }}>
                      {JSON.stringify(block, null, 2)}
                    </pre>
                  )}
                </div>
              </React.Fragment>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
