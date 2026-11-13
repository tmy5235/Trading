import { ref } from 'vue'

export function useVoice() {
  const choices=ref([]), selected=ref(''), summary=ref(null), status=ref('Voice stopped'), active=ref(false), busy=ref(false), audioAvailable=ref(false)
  let peer=null, stream=null, channel=null, audio=null, abort=null, startupTimer=null, generation=0
  async function select(id) {
    selected.value=id; summary.value=null
    if (!id) return
    try {
      const response=await fetch(`/api/voice/decisions/${encodeURIComponent(id)}`), data=await response.json()
      if (!response.ok) throw new Error(data.detail || 'Could not load decision')
      if (selected.value===id) summary.value=data
    } catch (err) { status.value=err.message }
  }
  async function refresh() {
    const response=await fetch('/api/voice/decisions')
    if (!response.ok) throw new Error('Could not load saved decisions')
    choices.value=await response.json()
    if (!choices.value.some(d=>d.id===selected.value)) selected.value=choices.value[0]?.id || ''
    if (selected.value) await select(selected.value)
    else summary.value=null
  }
  function stop(message='Voice stopped') {
    ++generation; clearTimeout(startupTimer); startupTimer=null; abort?.abort(); abort=null
    const oldChannel=channel, oldPeer=peer, oldStream=stream, oldAudio=audio
    channel=null; peer=null; stream=null; audio=null
    oldChannel?.close(); oldPeer?.close(); oldStream?.getTracks().forEach(track=>track.stop())
    if (oldAudio) { oldAudio.pause(); oldAudio.srcObject=null }
    active.value=false; busy.value=false; audioAvailable.value=false; status.value=message
  }
  async function start() {
    if (!selected.value || busy.value || active.value) return
    const current=++generation, decisionId=selected.value
    busy.value=true; status.value='Requesting microphone access…'
    try {
      const mic=await navigator.mediaDevices.getUserMedia({audio:true})
      if (current!==generation) { mic.getTracks().forEach(t=>t.stop()); return }
      stream=mic; active.value=true
      startupTimer=setTimeout(()=>{if(current===generation) stop('Voice connection timed out; microphone released.')},30000)
      const connection=new RTCPeerConnection(); peer=connection
      const player=new Audio(); player.autoplay=true; audio=player; audioAvailable.value=true
      connection.ontrack=event=>{ player.srcObject=new MediaStream([event.track]); player.play().catch(()=>{ status.value='Connected; select Play voice audio to hear the assistant.' }) }
      connection.onconnectionstatechange=()=>{ if (current===generation && ['failed','closed','disconnected'].includes(connection.connectionState)) stop('Voice disconnected; microphone released.') }
      mic.getAudioTracks().forEach(track=>connection.addTrack(track,mic))
      const events=connection.createDataChannel('oai-events'); channel=events
      events.onmessage=event=>{ try { const data=JSON.parse(event.data); if (current===generation && data.type==='session.started') { clearTimeout(startupTimer); startupTimer=null; status.value='Connected; microphone active. Ask a question.' };  if (current===generation && data.type==='session.closed') stop('Voice session ended; microphone released.') } catch (_) {} }
      events.onclose=()=>{ if (current===generation) stop('Voice disconnected; microphone released.') }
      const offer=await connection.createOffer(); await connection.setLocalDescription(offer)
      if (connection.iceGatheringState!=='complete') await new Promise((resolve,reject)=>{
        const timer=setTimeout(()=>{connection.removeEventListener('icegatheringstatechange',changed);reject(new Error('Timed out gathering connection candidates'))},10000)
        function changed(){ if(connection.iceGatheringState==='complete'){clearTimeout(timer);connection.removeEventListener('icegatheringstatechange',changed);resolve()} }
        connection.addEventListener('icegatheringstatechange',changed)
      })
      if (current!==generation) return
      status.value='Connecting to voice service…'; abort=new AbortController()
      const response=await fetch('/api/voice/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({decision_id:decisionId,sdp:connection.localDescription.sdp}),signal:abort.signal})
      const data=await response.json()
      if (!response.ok) throw new Error(data.detail || 'Voice connection failed')
      if (current!==generation) return
      await connection.setRemoteDescription({type:'answer',sdp:data.sdp})
      if (status.value !== 'Connected; microphone active. Ask a question.') status.value='Waiting for voice session…'
      busy.value=false
    } catch (err) {
      if (current===generation) stop(`Voice could not start: ${err.name==='NotAllowedError' ? 'microphone access was denied' : err.message}. Microphone released.`)
    }
  }
  function play(){ audio?.play().catch(()=>{status.value='Audio playback is unavailable.'}) }
  return {choices,selected,summary,status,active,busy,audioAvailable,select,refresh,start,stop,play}
}
