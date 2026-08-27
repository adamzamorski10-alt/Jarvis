import { useState } from 'react';
import { useAssistant } from '../../hooks/useAI';

export function AssistantView() {
  const { status, label, conversations, active, isLoading, isSending, error, newConversation, selectConversation, send } = useAssistant();
  const [draft, setDraft] = useState('');

  const submit = async () => {
    const text = draft.trim();
    if (!text || isSending) return;
    setDraft('');
    await send(text);
  };

  return (
    <div className="assistant-view fade-in">
      <aside className="assistant-sidebar">
        <button className="btn btn-primary" onClick={newConversation}>New Conversation</button>
        <div className="assistant-conversation-list">
          {conversations.map(item => (
            <button key={item.id} className={`assistant-conversation${active?.id === item.id ? ' active' : ''}`} onClick={() => selectConversation(item.id)}>
              <span>{item.title}</span>
              <small>{item.model ?? 'No model'}</small>
            </button>
          ))}
        </div>
      </aside>
      <section className="assistant-main">
        <header className="assistant-header">
          <div>
            <h1 className="view-title">Assistant</h1>
            <p className="view-subtitle">Text-only AI chat</p>
          </div>
          <div className={`badge ${status?.ai_ready ? 'badge-success' : ['online', 'ready'].includes(status?.provider_status ?? '') ? 'badge-warning' : 'badge-error'}`}>{label}</div>
          <div className="assistant-meta"><span>{status?.provider ?? 'ollama'}</span><span>{status?.model ?? 'No model selected'}</span></div>
        </header>
        <div className="assistant-messages">
          {isLoading ? <div className="spinner" /> : active?.messages.length ? active.messages.map(message => (
            <div key={message.id} className={`assistant-message ${message.role}`}>
              <div className="assistant-message-role">{message.role}</div>
              <div className="assistant-message-content selectable">{message.content}</div>
            </div>
          )) : <div className="assistant-empty">Start a new text-only conversation.</div>}
          {isSending && <div className="assistant-empty">Waiting for model response...</div>}
          {error && <div className="assistant-error">{error}</div>}
        </div>
        <footer className="assistant-inputbar">
          <textarea
            className="assistant-input"
            value={draft}
            placeholder={status?.ai_ready ? 'Message Assistant' : label}
            onChange={event => setDraft(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                submit();
              }
            }}
          />
          <button className="btn btn-primary" onClick={submit} disabled={!draft.trim() || isSending || !status?.ai_ready}>Send</button>
        </footer>
      </section>
    </div>
  );
}
