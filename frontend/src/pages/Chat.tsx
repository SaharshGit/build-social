// The home page: a chat with the BuildSocial assistant (layout from the Canva design).
//   /                     a new, empty chat (the conversation is created on the first message)
//   /chat/:conversationId an existing chat
import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import type { Asset, Message } from '../api/client'
import {
  useBrands, useConversation, useCreateConversation, useDeleteAsset, useDeleteConversation, useSendMessage,
  useUpdateConversation, useUploadAsset,
} from '../api/hooks'
import ChatCard, { type Attachment } from '../components/ChatCards'
import { PhotoChip, PhotoNote } from '../components/ChatPhotos'
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
  const [photos, setPhotos] = useState<Asset[]>([]) // attached to the message being written
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
      await send.mutateAsync({ conversationId: id, text, photoIds: photos.map((a) => a.id) })
      setPhotos([])
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

        {isNew && messages.length === 0 && !sending && <EmptyState onPick={setDraft} />}

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
        photos={photos}
        onPhotos={setPhotos}
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
  photos: Asset[]
  onPhotos: (photos: Asset[]) => void
  placeholder: string
}

/** The pill message box from the design. "+" opens brand / platform choices. */
function Composer(p: ComposerProps) {
  const brands = useBrands()
  const [menuOpen, setMenuOpen] = useState(false)
  const brandName = (brands.data ?? []).find((b) => b.id === p.brandId)?.name
  const upload = useUploadAsset(p.brandId)
  const removePhoto = useDeleteAsset(p.brandId)

  // Uploaded "for one post": it joins the brand library only if the person saves it there later.
  const attach = (file: File | undefined) => {
    if (!file) return
    upload.mutate({ file, kind: 'other', inLibrary: false }, { onSuccess: (asset) => p.onPhotos([...p.photos, asset]) })
  }
  const detach = (asset: Asset) => {
    p.onPhotos(p.photos.filter((a) => a.id !== asset.id))
    removePhoto.mutate(asset.id) // never sent, so nothing else uses it
  }

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
          <div className="plus-menu-label">Photo</div>
          {p.brandId ? (
            <label className={`plus-menu-file${p.photos.length >= 4 ? ' disabled' : ''}`}>
              Attach photo <span className="tiny muted">PNG, JPG or WebP, up to 10 MB</span>
              <input type="file" accept="image/png,image/jpeg,image/webp" hidden disabled={p.photos.length >= 4}
                onChange={(e) => { attach(e.target.files?.[0]); e.target.value = ''; setMenuOpen(false) }} />
            </label>
          ) : (
            <button disabled>Attach photo (choose a brand first)</button>
          )}
        </div>
      )}
      {(p.photos.length > 0 || upload.isPending) && (
        <div className="composer-photos">
          {p.photos.map((a) => <PhotoChip key={a.id} asset={a} onRemove={() => detach(a)} />)}
          {upload.isPending && <span className="photo-chip muted tiny"><span className="spinner" /> Uploading and reading…</span>}
        </div>
      )}
      {upload.error && <ErrorBox error={upload.error} />}
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

/** The new-chat screen: what BuildSocial can do, as questions you can click (they fill the box, you send). */
function EmptyState({ onPick }: { onPick: (text: string) => void }) {
  const brands = useBrands()
  const names = (brands.data ?? []).map((b) => b.name)
  const [a, b, c] = [names[0] ?? 'my brand', names[1] ?? names[0] ?? 'my brand', names[2] ?? names[0] ?? 'my brand']
  const groups = [
    {
      title: 'Create',
      items: [
        `Create an Instagram post for ${a} about this week's special`,
        `Write a LinkedIn post for ${b} about what makes us different`,
      ],
    },
    {
      title: 'Review',
      items: ['Show me the posts waiting for review', `Show me the latest posts for ${c}`],
    },
    {
      title: 'Ask',
      items: ['How many posts are pending?', 'What did we spend this week?', 'Did any builds fail recently?'],
    },
  ]
  return (
    <div className="chat-empty">
      <h2>What should we create today?</h2>
      <p>Ask for a post, review what's waiting, or ask about your posts and spending.</p>
      <div className="suggestion-groups">
        {groups.map((g) => (
          <div key={g.title} className="suggestion-group">
            <div className="suggestion-title">{g.title}</div>
            {g.items.map((q) => (
              <button key={q} className="suggestion" onClick={() => onPick(q)}>{q}</button>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

function ChatMessage({ message, onSend }: { message: Message; onSend?: (text: string) => void }) {
  const attachments = (message.attachments ?? []) as Attachment[]
  const photos = attachments.filter((a) => a.type === 'photo')
  return (
    <div className={`msg msg-${message.role}`}>
      <div className="bubble">{message.content}</div>
      {photos.map((a) => <div key={a.id} className="msg-card"><PhotoNote assetId={a.id!} /></div>)}
      {attachments.filter((a) => a.type !== 'photo').map((a, i) => (
        <div key={i} className="msg-card"><ChatCard attachment={a} onSend={onSend} /></div>
      ))}
    </div>
  )
}
