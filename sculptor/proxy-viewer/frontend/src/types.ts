export interface ProxyEvent {
  id: string;
  request_id?: string;  // Added for duplicate detection
  timestamp: string;
  user_message: string | null;
  request: {
    model: string;
    messages: any[];
    temperature?: number;
    max_tokens?: number;
    [key: string]: any;
  };
  response: {
    content?: Array<{
      type: string;
      text?: string;
      name?: string;
      id?: string;
      input?: any;
    }>;
    usage?: {
      input_tokens: number;
      output_tokens: number;
    };
    [key: string]: any;
  };
  duration_ms: number;
  token_counts: {
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
  };
  from_cache: boolean;
  is_duplicate?: boolean;
  duplicate_of?: string;
}

export type ConnectionStatus = 'connecting' | 'connected' | 'disconnected' | 'error';

export interface WebSocketMessage {
  type?: string;
  message?: string;
  timestamp?: string;
  id?: string;
  request_id?: string;
  text?: string;
  user_message?: string;
  request?: any;
  response?: any;
  duration_ms?: number;
  token_counts?: {
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
  };
}
