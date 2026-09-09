import { useCallback, useEffect, useState } from 'react';
import {
  deleteConversation,
  fetchConversations,
} from '../services/conversations';
import {
  getCurrentSessionId,
  newSessionId,
  setCurrentSessionId,
} from '../services/session';
import { ConversationMeta } from '../types/events';

export interface Sessions {
  sessionId: string;
  conversations: ConversationMeta[];
  error: string | null;
  refresh: () => void;
  select: (sessionId: string) => void;
  startNew: () => void;
  remove: (sessionId: string) => Promise<void>;
}

/** 会话状态(A2):当前会话 id(localStorage 恢复)+ 列表 + 切换/新建/删除。 */
export function useSessions(): Sessions {
  const [sessionId, setSessionId] = useState<string>(getCurrentSessionId);
  const [conversations, setConversations] = useState<ConversationMeta[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    fetchConversations()
      .then((rows) => {
        setConversations(rows);
        setError(null);
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : String(reason));
      });
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const select = useCallback((next: string) => {
    setCurrentSessionId(next);
    setSessionId(next);
  }, []);

  const startNew = useCallback(() => select(newSessionId()), [select]);

  const remove = useCallback(
    async (target: string) => {
      setError(null);
      try {
        await deleteConversation(target);
        if (target === sessionId) select(newSessionId());
        refresh();
      } catch (reason: unknown) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    },
    [refresh, select, sessionId],
  );

  return { sessionId, conversations, error, refresh, select, startNew, remove };
}
