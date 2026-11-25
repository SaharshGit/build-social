// The home page: a chat with the BuildSocial assistant (layout from the Canva design).
//   /                     a new, empty chat (the conversation is created on the first message)
//   /chat/:conversationId an existing chat
import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import type { Message } from '../api/client'
import {
  useBrands, useConversation, useCreateConversation, useDeleteConversation, useSendMessage, useUpdateConversation,
} from '../api/hooks'
import ChatCard, { type Attachment } from '../components/ChatCards'
import { ErrorBox, Loading } from '../components/common'
import { PencilIcon, PlusIcon, SendIcon, TrashIcon } from '../components/Icons'
import { PLATFORM_LABELS } from '../lib/format'

export default function Chat() {
  const { conversationId } = useParams()
  const navigate = useNavigate()
  const conversation = useConversation(conversationId)
  const create = useCreateConversation()
  const send = useSendMessage()
  const update = useUpdateConversation(conversationId ?? '')
  const remove = useDeleteConversation()
  const [renaming, setRenaming] = useState<string | null>(null) // the title being edited
  const [draft, setDraft] = useState('')
  const [newChatBrand, setNewChatBrand] = useState('') // brand picked before the chat exists
  const [platform, setPlatform] = useState('') // picked in the + menu; added to the message when it isn't said
  const [pendingText, setPendingText] = useState<string | null>(null) // shown until the reply arrives
  const bottom = useRef<HTMLDivElement>(null)

  const messages = conversation.data?.messages ?? []
  const sending = pendingText !== null
  const isNew = !conversationId
  const brandId = conversationId ? (conversation.data?.brand_id ?? '') : newChatBrand

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length, sending])

  async function submit(text: string) {
    text = text.trim()
    if (!text || sending) return
    if (platform && !/instagram|linkedin/i.test(text)) text += `\n\nPlatform: ${PLATFORM_LABELS[platform]}`
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

  function chooseBrand(id: string) {
    if (conversationId) update.mutate({ brand_id: id || null })
    else setNewChatBrand(id)
  }

  return (
    <section className={`chat${isNew && messages.length === 0 && !sending ? ' chat-new' : ''}`}>
      {conversation.data && (
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
            <h1>{conversation.data.title}</h1>
          )}
          {renaming === null && (
            <>
              <button className="icon-btn" title="Rename chat" onClick={() => setRenaming(conversation.data!.title)}>
                <PencilIcon />
              </button>
              <button
                className="icon-btn"
                title="Delete chat"
                onClick={() => {
                  if (!confirm('Delete this chat? Posts created from it are kept.')) return
                  remove.mutate(conversation.data!.id, { onSuccess: () => navigate('/') })
                }}
              >
                <TrashIcon />
              </button>
            </>
          )}
        </header>
      )}

      <div className="thread">
        {conversationId && conversation.isLoading && <Loading what="Loading chat" />}
        {conversation.error && <ErrorBox error={conversation.error} />}

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

      <Composer
        draft={draft}
        setDraft={setDraft}
        onSubmit={() => submit(draft)}
        sending={sending}
        brandId={brandId}
        onBrand={chooseBrand}
        platform={platform}
        onPlatform={setPlatform}
        placeholder={isNew ? 'Ask BuildSocial to create, review or schedule a post…' : 'Reply to BuildSocial…'}
      />
    </section>
  )
}

type ComposerProps = {
  draft: string
  setDraft: (s: string) => void
  onSubmit: () => void
  sending: boolean
  brandId: string
  onBrand: (id: string) => void
  platform: string
  onPlatform: (p: string) => void
  placeholder: string
}

/** The pill message box from the design. "+" opens brand / platform choices. */
function Composer(p: ComposerProps) {
  const brands = useBrands()
  const [menuOpen, setMenuOpen] = useState(false)
  const brandName = (brands.data ?? []).find((b) => b.id === p.brandId)?.name

  return (
    <div className="composer-wrap">
      {menuOpen && (
        <div className="plus-menu" onMouseLeave={() => setMenuOpen(false)}>
          <div className="plus-menu-label">Brand for this chat</div>
          {(brands.data ?? []).map((b) => (
            <button key={b.id} className={b.id === p.brandId ? 'selected' : ''}
                    onClick={() => { p.onBrand(b.id === p.brandId ? '' : b.id); setMenuOpen(false) }}>
              {b.name}{b.id === p.brandId && <span>✓</span>}
            </button>
          ))}
          <div className="plus-menu-label">Platform</div>
          {Object.entries(PLATFORM_LABELS).map(([key, label]) => (
            <button key={key} className={key === p.platform ? 'selected' : ''}
                    onClick={() => { p.onPlatform(key === p.platform ? '' : key); setMenuOpen(false) }}>
              {label}{key === p.platform && <span>✓</span>}
            </button>
          ))}
          <button disabled>Attach a file (soon)</button>
        </div>
      )}
      {(brandName || p.platform) && (
        <div className="composer-context">
          {brandName && <span className="badge">{brandName}</span>}
          {p.platform && <span className="badge">{PLATFORM_LABELS[p.platform]}</span>}
        </div>
      )}
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault()
          p.onSubmit()
        }}
      >
        <button type="button" className={`composer-plus${menuOpen ? ' open' : ''}`} title="Brand and platform"
                onClick={() => setMenuOpen((o) => !o)}>
          <PlusIcon size={26} />
        </button>
        <textarea
          rows={1}
          value={p.draft}
          placeholder={p.placeholder}
          onChange={(e) => {
            p.setDraft(e.target.value)
            e.target.style.height = 'auto'
            e.target.style.height = `${e.target.scrollHeight}px` // grow with the text
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              p.onSubmit()
            }
          }}
          autoFocus
        />
        <button className="send-btn" disabled={p.sending || !p.draft.trim()} title="Send (Enter)">
          <SendIcon />
        </button>
      </form>
    </div>
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
