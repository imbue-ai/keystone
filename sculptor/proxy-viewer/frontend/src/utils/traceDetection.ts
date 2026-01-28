import { ProxyEvent } from '../types';

export interface AgentTrace {
  id: string;
  events: ProxyEvent[];
  startTime: string;
  endTime?: string;
  parentTraceId?: string;  // If this trace was spawned from another
  parentEventId?: string;   // Which event in the parent spawned this
  depth: number;
}

export interface TracedEvents {
  traces: AgentTrace[];
}

/**
 * Normalize message content by removing cache_control fields
 * This is needed because Claude Code dynamically adds/removes cache_control
 */
function normalizeContent(content: any): any {
  if (typeof content === 'string') {
    return content;
  }

  if (Array.isArray(content)) {
    return content.map(item => {
      if (typeof item === 'object' && item !== null) {
        // Create a copy without cache_control
        const { cache_control, ...rest } = item;
        return rest;
      }
      return item;
    });
  }

  if (typeof content === 'object' && content !== null) {
    // Single object content (less common)
    const { cache_control, ...rest } = content;
    return rest;
  }

  return content;
}

/**
 * Check if messages1 is a verbatim prefix of messages2
 * This means messages2 contains all of messages1 in the same order, plus potentially more
 */
function isMessagesPrefix(messages1: any[], messages2: any[]): boolean {
  if (!messages1 || !messages2) {
    return false;
  }
  if (messages1.length > messages2.length) {
    return false;
  }

  // Check each message in messages1 exists identically in messages2
  for (let i = 0; i < messages1.length; i++) {
    const msg1 = messages1[i];
    const msg2 = messages2[i];

    // Check role matches
    if (msg1.role !== msg2.role) {
      return false;
    }

    // Normalize content to remove cache_control before comparing
    const normalizedContent1 = normalizeContent(msg1.content);
    const normalizedContent2 = normalizeContent(msg2.content);

    // Check content matches (deep equality on normalized content)
    const content1Str = JSON.stringify(normalizedContent1);
    const content2Str = JSON.stringify(normalizedContent2);
    if (content1Str !== content2Str) {
      return false;
    }
  }

  return true;
}

/**
 * Group events into traces based on prefix relationships
 */
export function detectTraces(events: ProxyEvent[]): TracedEvents {
  if (events.length === 0) {
    return { traces: [] };
  }

  // IMPORTANT: Events come in reverse chronological order (newest first)
  // We need to process them in chronological order for prefix detection to work
  const chronologicalEvents = [...events].reverse();

  const traces: AgentTrace[] = [];
  const eventToTrace = new Map<string, AgentTrace>();

  chronologicalEvents.forEach((event, index) => {
    const currentMessages = event.request.messages || [];

    let foundTrace: AgentTrace | null = null;
    let parentInfo: { traceId: string; eventId: string } | null = null;

    // Look backwards through recent events to find one this extends
    for (let i = index - 1; i >= Math.max(0, index - 10); i--) {
      const prevEvent = chronologicalEvents[i];
      const prevMessages = prevEvent.request.messages || [];

      if (isMessagesPrefix(prevMessages, currentMessages)) {
        // This event extends the previous one
        const prevTrace = eventToTrace.get(prevEvent.id);
        if (prevTrace) {
          foundTrace = prevTrace;
          break;
        }
      }
    }

    // If we didn't find a trace this extends, check if this might be spawned from a tool call
    if (!foundTrace && index > 0) {
      // Look for a recent event that had tool_use in its response
      for (let i = index - 1; i >= Math.max(0, index - 5); i--) {
        const prevEvent = chronologicalEvents[i];
        const hasToolUse = prevEvent.response?.content?.some(c =>
          c.type === 'tool_use' && (c.name === 'Task' || c.name === 'Agent')
        );

        if (hasToolUse) {
          const parentTrace = eventToTrace.get(prevEvent.id);
          if (parentTrace) {
            parentInfo = { traceId: parentTrace.id, eventId: prevEvent.id };
            break;
          }
        }
      }
    }

    if (foundTrace) {
      // Add to existing trace
      foundTrace.events.push(event);
      foundTrace.endTime = event.timestamp;
      eventToTrace.set(event.id, foundTrace);
    } else {
      // Create new trace
      const newTrace: AgentTrace = {
        id: `trace-${traces.length}`,
        events: [event],
        startTime: event.timestamp,
        endTime: event.timestamp,
        parentTraceId: parentInfo?.traceId,
        parentEventId: parentInfo?.eventId,
        depth: parentInfo ? (traces.find(t => t.id === parentInfo.traceId)?.depth || 0) + 1 : 0
      };

      traces.push(newTrace);
      eventToTrace.set(event.id, newTrace);
    }
  });

  return { traces };
}


/**
 * Calculate the message delta between two events in a trace
 */
export function getMessageDelta(prevEvent: ProxyEvent | null, currentEvent: ProxyEvent): {
  addedMessages: any[];
  isToolResult: boolean;
} {
  if (!prevEvent) {
    return {
      addedMessages: currentEvent.request.messages || [],
      isToolResult: false
    };
  }

  const prevMessages = prevEvent.request.messages || [];
  const currentMessages = currentEvent.request.messages || [];

  // Get the new messages (those beyond the previous length)
  const addedMessages = currentMessages.slice(prevMessages.length);

  // Check if any of the added messages contain tool results
  const isToolResult = addedMessages.some(msg =>
    msg.role === 'user' &&
    Array.isArray(msg.content) &&
    msg.content.some(block => block.type === 'tool_result')
  );

  return { addedMessages, isToolResult };
}
