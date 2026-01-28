import clsx from 'clsx';

export function MessageContent({ content, toolIdToName = {} }: { content: any, toolIdToName?: Record<string, string> }) {
  if (typeof content === 'string') {
    return <div className="text-sm text-gray-800 whitespace-pre-wrap font-sans leading-relaxed overflow-auto max-h-96">{content}</div>;
  }

  if (Array.isArray(content)) {
    // Build a map of tool IDs to names from tool_use blocks within this content
    const localToolIdToName: Record<string, string> = { ...toolIdToName };
    content.forEach(block => {
      if (block.type === 'tool_use' && block.id && block.name) {
        localToolIdToName[block.id] = block.name;
      }
    });

    return (
      <div className="space-y-3">
        {content.map((block, i) => (
          <>
          {i > 0 && <div className="border-t-2 border-gray-200 my-3" />}
          <div key={i} className={clsx(
            "text-sm rounded-lg",
            block.type === 'text' && "p-0",
            block.type === 'tool_use' && "bg-blue-50 border border-blue-200 p-3",
            block.type === 'tool_result' && (block.is_error ? "bg-red-50 border border-red-200 p-3" : "bg-green-50 border border-green-200 p-3")
          )}>
            {block.type === 'text' && (
              <div className="text-gray-800 whitespace-pre-wrap font-sans leading-relaxed pb-1 overflow-auto max-h-96">
                {typeof block.text === 'string' ? block.text : JSON.stringify(block.text)}
              </div>
            )}
            {block.type === 'tool_use' && (
              <div>
                <div className="text-blue-600 text-xs font-bold uppercase tracking-wide mb-2">
                  {block.name} Call
                </div>
                <pre className="text-gray-700 text-xs overflow-auto max-h-48 font-mono bg-white/50 p-2 rounded">
                  {JSON.stringify(block.input, null, 2)}
                </pre>
              </div>
            )}
            {block.type === 'tool_result' && (
              <div>
                <div className={clsx("text-xs font-bold uppercase tracking-wide mb-2", block.is_error ? "text-red-600" : "text-green-600")}>
                  {localToolIdToName[block.tool_use_id] || 'Unknown'} Result
                </div>
                <pre className="text-gray-700 text-xs overflow-auto max-h-48 p-3 bg-white/70 rounded border border-gray-200 font-mono">
                  {typeof block.content === 'string'
                    ? block.content
                    : JSON.stringify(block.content || block, null, 2)}
                </pre>
              </div>
            )}
          </div>
          </>
        ))}
      </div>
    );
  }

  return <pre className="text-sm text-gray-600 whitespace-pre-wrap font-mono p-3 bg-gray-50 rounded-lg overflow-auto max-h-96">{JSON.stringify(content, null, 2)}</pre>;
}
