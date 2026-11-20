import { defineComponent, h, nextTick, ref } from 'vue'

export const assessmentQuestion = 'Request a new decision for the captured chart.'

export async function matchesCapture(result, snapshot) {
  if (!snapshot) return false
  const {context} = snapshot
  if (!['symbol', 'interval', 'mode', 'last_completed_candle', 'forming_candle'].every(
    key => result.context?.[key] === context[key]
  ) || Date.parse(result.context.capture_time) !== Date.parse(context.capture_time)) return false
  const bytes = Uint8Array.from(atob(snapshot.image_url.split(',')[1]), char => char.charCodeAt(0))
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))
  return Array.from(digest, byte => byte.toString(16).padStart(2, '0')).join('') === result.png_sha256
}

export default defineComponent({
  name: 'ChartChat',
  props: {
    request: { type: Function, required: true },
    busy: Boolean,
  },
  setup(props) {
    const draft = ref(''), turns = ref([]), log = ref(null), input = ref(null)
    let conversationId = crypto.randomUUID()
    function newChat() {
      conversationId = crypto.randomUUID()
      turns.value = []
      draft.value = ''
      input.value?.focus()
    }
    async function scrollToReply() {
      await nextTick()
      if (log.value) log.value.scrollTop = log.value.scrollHeight
    }
    async function send(text) {
      const question = text.trim()
      if (!question || props.busy || turns.value.at(-1)?.pending) return
      const history = turns.value.filter(turn => turn.message).slice(-6).flatMap(turn => [
        {role:'user', content:turn.question}, {role:'assistant', content:turn.message.slice(0, 2400)},
      ])
      draft.value = ''
      turns.value.push({question, pending:true})
      const turn = turns.value.at(-1)
      try {
        const reply = await props.request(question, history, conversationId)
        turn.message = reply.message || reply.detail || 'The agent returned no response.'
        turn.tools = reply.tools || []
        turn.stale = reply.stale
      } catch (error) {
        turn.message = error.message
        turn.tools = []
      } finally {
        turn.pending = false
        await scrollToReply()
      }
    }
    return () => h('section', {class:'chat-panel', 'aria-labelledby':'chat-title'}, [
      h('div', {class:'chat-heading'}, [
        h('h2', {id:'chat-title'}, 'Chart chat'),
        h('button', {type:'button', disabled:props.busy || turns.value.at(-1)?.pending, onClick:newChat}, 'New chat'),
      ]),
      h('div', {ref:log, class:'chat-log', role:'log', 'aria-label':'Conversation', 'aria-live':'polite'}, turns.value.map((turn, index) =>
        h('article', {key:index, class:'chat-turn'}, [
          h('p', {class:'chat-question'}, turn.question),
          h('p', {class:'chat-reply'}, turn.pending ? 'Reading your message…' : turn.message),
          turn.stale ? h('p', {class:'chat-note'}, 'The chart changed. This reply belongs to the earlier capture.') : null,
          turn.tools ? h('details', {class:'chat-tools'}, [
            h('summary', 'Tool calls (' + turn.tools.length + ')'),
            ...turn.tools.map((call, i) => h('div', {key:i}, [
              h('strong', call.name),
              h('pre', JSON.stringify({arguments:call.arguments, result:call.result}, null, 2)),
              call.result?.image_url ? h('a', {href:call.result.image_url, target:'_blank', rel:'noopener'}, 'Open captured chart') : null,
              call.result?.previous_assessment?.image_url ? h('a', {href:call.result.previous_assessment.image_url,
                class:'previous-capture', target:'_blank', rel:'noopener'}, 'Open earlier chart') : null,
            ])),
            turn.tools.length ? null : h('p', 'No tools called.'),
          ]) : null,
        ])
      )),
      h('div', {class:'chat-suggestions'}, [
        h('button', {type:'button', disabled:props.busy, onClick:()=>send('Explain the chart signal rules.')}, 'Explain rules'),
        h('button', {type:'button', disabled:props.busy, onClick:()=>send(assessmentQuestion)}, 'Assess this chart'),
      ]),
      h('form', {class:'chat-composer', onSubmit:e=>{ e.preventDefault(); send(draft.value) }}, [
        h('input', {ref:input, type:'text', 'aria-label':'Message the chart assistant', placeholder:'Ask about the rules or this chart…',
          maxlength:1500, value:draft.value, onInput:e=>{ draft.value=e.target.value }}),
        h('button', {type:'submit', disabled:props.busy || !draft.value.trim()}, 'Send'),
      ]),
    ])
  },
})
