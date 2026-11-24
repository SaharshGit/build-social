// The home page: a chat with the BuildSocial assistant.
//   /                    a new, empty chat (the conversation is created on the first message)
//   /chat/:conversationId an existing chat
import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import type { Message } from '../api/client'
import {
  useBrands, useConversation, useCreateConversation, useDeleteConversation, useSendMessage, useUpdateConversation,
} from '../api/hooks'
import ChatCard, { type Attachment } from '../components/ChatCards'
import { ErrorBox, Loading } from '../components/common'

const SUGGESTIONS = [
  'Create an Instagram post about monsoon evenings',
  'How many posts are pending review?',
  'What did we spend this week?',
  'Show me the latest posts',
]

export default function Chat() {
  const { conversationId } = useParams()
  const navigate = useNavigate()
  const conversation = useConversation(conversationId)
  const brands = useBrands()
  const create = useCreateConversation()
  const send = useSendMessage()
  const update = useUpdateConversation(conversationId ?? '')
  const remove = useDeleteConversation()
  const [renaming, setRenaming] = useState<string | null>(null) // the title being edited
  const [draft, setDraft] = useState('')
  const [newChatBrand, setNewChatBrand] = useState('') // brand picked before the chat exists
  const [pendingText, setPendingText] = useState<string | null>(null) // shown until the reply arrives
  const bottom = useRef<HTMLDivElement>(null)

  const messages = conversation.data?.messages ?? []
  const sending = pendingText !== null

  // Keep the newest message in view.
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length, sending])

  async function submit(text: string) {
    text = text.trim()
    if (!text || sending) return
    setPendingText(text)
    setDraft('')
    let id = conversationId
    try {
      if (!id) id = (await create.mutateAsync(newChatBrand || null)).id
      await send.mutateAsync({ conversationId: id, text })
    } catch {
      setDraft(text) // give the text back so nothing typed is lost; the error shows below
    } finally {
      setPendingText(null)
    }
    if (id && id !== conversationId) navigate(`/chat/${id}`)
  }

  const brandId = conversationId ? (conversation.data?.brand_id ?? '') : newChatBrand

  return (
    <section className="chat">
      <header className="chat-header">
        {renaming !== null ? (
          <form
            className="rename"
            onSubmit={(e) => {
              e.preventDefault()
              if (renaming.trim()) update.mutate({ title: renaming.trim() })
              setRenaming(null)
            }}
          >
            <input value={renaming} onChange={(e) => setRenaming(e.target.value)} autoFocus maxLength={200}
                   onBlur={() => setRenaming(null)} onKeyDown={(e) => e.key === 'Escape' && setRenaming(null)} />
          </form>
        ) : (
          <h1>{conversation.data?.title ?? 'New chat'}</h1>
        )}
        {conversation.data && renaming === null && (
          <>
            <button className="icon-btn" title="Rename chat" onClick={() => setRenaming(conversation.data!.title)}>✎</button>
            <button
              className="icon-btn"
              title="Delete chat"
              onClick={() => {
                if (!confirm('Delete this chat? Posts created from it are kept.')) return
                remove.mutate(conversation.data!.id, { onSuccess: () => navigate('/') })
              }}
            >
              🗑
            </button>
          </>
        )}
        <label className="brand-chip">
          Brand
          <select
            value={brandId}
            disabled={update.isPending}
            onChange={(e) =>
              conversationId ? update.mutate({ brand_id: e.target.value || null }) : setNewChatBrand(e.target.value)
            }
          >
            <option value="">(none yet)</option>
            {(brands.data ?? []).map((b) => (
              <option key={b.id} value={b.id}>{b.name}</option>
            ))}
          </select>
        </label>
      </header>

      <div className="thread">
        {conversationId && conversation.isLoading && <Loading what="Loading chat" />}
        {conversation.error && <ErrorBox error={conversation.error} />}

        {!conversationId && messages.length === 0 && !sending && (
          <div className="chat-empty">
            <h2>What should we work on?</h2>
            <p className="muted">
              Ask me to create or change a post, approve or reject one, or ask about your posts and spending.
            </p>
            <div className="suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="btn" onClick={() => setDraft(s)}>{s}</button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m) => <ChatMessage key={m.id} message={m} onSend={sending ? undefined : submit} />)}

        {pendingText && (
          <>
            <div className="msg msg-user"><div className="bubble">{pendingText}</div></div>
            <div className="msg msg-assistant"><div className="bubble muted"><span className="spinner" /> Thinking…</div></div>
          </>
        )}
        {(create.error || send.error) && !sending && <ErrorBox error={create.error ?? send.error} />}
        <div ref={bottom} />
      </div>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault()
          submit(draft)
        }}
      >
        <textarea
          rows={2}
          value={draft}
          placeholder="Message BuildSocial…  (Enter to send, Shift+Enter for a new line)"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              submit(draft)
            }
          }}
          autoFocus
        />
        <button className="btn btn-primary" disabled={sending || !draft.trim()}>Send</button>
      </form>
    </section>
  )
}

function ChatMessage({ message, onSend }: { message: Message; onSend?: (text: string) => void }) {
  const attachments = (message.attachments ?? []) as Attachment[]
  return (
    <div className={`msg msg-${message.role}`}>
      <div className="bubble">{message.content}</div>
      {attachments.map((a, i) => (
        <div key={i} className="msg-card"><ChatCard attachment={a} onSend={onSend} /></div>
      ))}
    </div>
  )
}
