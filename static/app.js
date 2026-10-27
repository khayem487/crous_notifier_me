const button=document.getElementById('check-button');
const label=document.getElementById('button-label');
const card=document.getElementById('result-card');
const form=document.getElementById('search-form');
const byId=id=>document.getElementById(id);
const labels={AVAILABLE:['Signal de disponibilité','Signal explicite','↗'],UNAVAILABLE:["Absence d'offres indiquée",'Aucune offre indiquée','—'],UNKNOWN:['Disponibilité indéterminée','Indéterminé','?']};
form.addEventListener('submit',async event=>{
  event.preventDefault();
  button.disabled=true;
  card.setAttribute('aria-busy','true');
  label.textContent='Vérification en cours…';
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),20000);
  try{
    const search={source_url:byId('source-url').value.trim(),zone:byId('search-zone').value.trim(),criteria:byId('search-criteria').value.trim()};
    const response=await fetch('/api/check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(search),signal:controller.signal});
    if(!response.ok){
      const problem=await response.json().catch(()=>({}));
      throw new Error(problem.error||'Le service ne peut pas effectuer ce contrôle pour le moment.');
    }
    const result=await response.json();
    const [title,badge,icon]=labels[result.state]||labels.UNKNOWN;
    card.dataset.state=result.state;
    byId('result-title').textContent=title;
    byId('state-badge').textContent=badge;
    byId('result-icon').textContent=icon;
    byId('result-evidence').textContent=result.evidence;
    byId('result-count').textContent=result.count===null?'Non déterminé':String(result.count);
    byId('result-time').textContent=new Intl.DateTimeFormat('fr-FR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(result.checked_at));
    byId('result-fetch').textContent=result.fetch_ok?'Reçue':'Non exploitable';
    byId('result-scope').textContent=result.search.zone?`Zone : ${result.search.zone}${result.search.criteria?` · Critères : ${result.search.criteria}`:''}`:'Portail public · aucune zone configurée';
    label.textContent='Consulter à nouveau';
  }catch(error){
    card.dataset.state='UNKNOWN';
    byId('result-title').textContent='Vérification indisponible';
    byId('state-badge').textContent='Non vérifié';
    byId('result-icon').textContent='?';
    byId('result-evidence').textContent=error.name==='AbortError'?"Le contrôle n'a pas abouti à temps. Aucun signal de disponibilité ne peut être annoncé.":error.message;
    byId('result-count').textContent='Non déterminé';
    byId('result-fetch').textContent='Non vérifiée';
    byId('result-time').textContent='—';
    byId('result-scope').textContent='Aucun résultat pour cette configuration.';
    label.textContent='Réessayer la vérification';
  }finally{
    clearTimeout(timer);
    button.disabled=false;
    card.setAttribute('aria-busy','false');
  }
});
