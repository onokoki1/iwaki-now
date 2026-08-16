const fallbackNews = [
  {id:"seed-1",title:"いわき七夕まつり、きょう最終日　平商店街を彩る夏の風物詩",summary:"平商店街で開催中の「いわき七夕まつり」は8月8日が最終日。市の案内では10時から20時30分まで開催予定で、同日夕方にはいわきおどりも行われます。",category:"イベント",area:"平",publishedAt:"2026-08-08T08:00:00+09:00",source:"いわき市",sourceUrl:"https://www.city.iwaki.lg.jp/www/genre/1000100000345/index.html",priorityScore:25,coverageCount:1},
  {id:"seed-2",title:"いわきおどり、きょう17時から　駅前大通りが熱気に包まれる",summary:"いわきの夏を代表する「いわきおどり」が8月8日、いわき駅前大通りで開催予定。",category:"イベント",area:"平",publishedAt:"2026-08-08T07:50:00+09:00",source:"いわき市",sourceUrl:"https://www.city.iwaki.lg.jp/www/genre/1000100000345/index.html",priorityScore:25,coverageCount:1},
  {id:"seed-3",title:"いわき駅前大通りで無料公衆Wi-Fi　8月3日から提供開始",summary:"いわき駅前大通りで無料公衆Wi-Fiの提供が始まりました。",category:"暮らし",area:"平",publishedAt:"2026-08-03T09:00:00+09:00",source:"いわき民報",sourceUrl:"https://iwaki-minpo.co.jp/news/2026/07/313779/",priorityScore:30,coverageCount:1},
  {id:"seed-4",title:"福島県知事選　いわき市の新人が立候補を表明",summary:"任期満了に伴う福島県知事選挙をめぐる動きです。",category:"市政",area:"全市",publishedAt:"2026-08-05T12:00:00+09:00",source:"福島テレビ",sourceUrl:"https://www.fukushima-tv.co.jp/",priorityScore:48,coverageCount:1},
  {id:"seed-5",title:"市内37公民館、後期の市民講座を案内",summary:"いわき市は、市内37公民館で実施する市民講座を公開しました。",category:"教育・子育て",area:"全市",publishedAt:"2026-07-31T10:00:00+09:00",source:"いわき市",sourceUrl:"https://www.city.iwaki.lg.jp/",priorityScore:32,coverageCount:1},
  {id:"seed-7",title:"勿来・國玉神社の「風鈴参道」　8月16日まで",summary:"勿来町窪田の國玉神社では、風鈴とこけ玉で彩る「風鈴参道」を開催中です。",category:"イベント",area:"勿来",publishedAt:"2026-07-28T11:00:00+09:00",source:"いわき民報",sourceUrl:"https://iwaki-minpo.co.jp/",priorityScore:16,coverageCount:1,isWeekendEvent:true},
  {id:"seed-8",title:"いわき市の海水浴場、8月16日まで開設",summary:"いわき市内の海水浴場は夏季開設中です。",category:"暮らし",area:"四倉",publishedAt:"2026-07-20T09:30:00+09:00",source:"いわき民報",sourceUrl:"https://iwaki-minpo.co.jp/",priorityScore:28,coverageCount:1},
  {id:"seed-9",title:"いわきFC応援キャンペーン　ブックエースで実施",summary:"地域ぐるみでクラブを後押しする企画が展開されます。",category:"スポーツ",area:"全市",publishedAt:"2026-08-07T12:00:00+09:00",source:"いわきFC",sourceUrl:"https://iwakifc.com/",priorityScore:25,coverageCount:1}
];

const regionAreas=["平","小名浜","勿来","常磐","内郷","四倉","遠野","小川","好間","三和","田人","川前","久之浜・大久"];
let news=fallbackNews,generatedAt=null,sourceCount=null,weekendInfo=null;
const state={category:"すべて",area:"すべて",query:"",sort:"new",special:null};
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const esc=s=>String(s??"").replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const safeUrl=v=>{try{const u=new URL(v,location.href);return /^(https?:|file:)$/.test(u.protocol)?u.href:'#'}catch{return '#'}};
const fmt=iso=>new Intl.DateTimeFormat('ja-JP',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(iso));
const timeAgo=iso=>{const m=Math.max(0,Math.floor((Date.now()-new Date(iso))/60000));if(m<60)return `${m}分前`;const h=Math.floor(m/60);if(h<24)return `${h}時間前`;return `${Math.floor(h/24)}日前`};
const score=n=>Number(n.priorityScore||0);
const detailUrl=n=>n.detailPath?safeUrl(n.detailPath):safeUrl(n.sourceUrl);
const coverage=n=>Math.max(1,Number(n.coverageCount||1));
const openingClosingFallback=n=>{const t=`${n.title||''} ${n.summary||''}`;return !/(オープンキャンパス|オープンデータ|オープンイノベーション|オープン戦|オープン大会|オープン講座)/i.test(t)&&/(開店|閉店|オープン|新店舗|新店|新規出店|移転オープン|リニューアルオープン|営業終了|閉館|閉鎖)/i.test(t)};

function sortedNews(){return [...news].sort((a,b)=>(state.sort==='old'?1:-1)*(new Date(a.publishedAt)-new Date(b.publishedAt)))}
function rankedNews(){return [...news].sort((a,b)=>score(b)-score(a)||new Date(b.publishedAt)-new Date(a.publishedAt))}
function isOpeningClosing(n){return Boolean(n.isOpeningClosing)||openingClosingFallback(n)}
function isWeekendEvent(n){return Boolean(n.isWeekendEvent)}
function filteredNews(){return sortedNews().filter(n=>(!state.special||(state.special==='openingClosing'?isOpeningClosing(n):isWeekendEvent(n)))&&(state.category==='すべて'||n.category===state.category)&&(state.area==='すべて'||n.area===state.area||n.area==='全市')&&(!state.query||`${n.title} ${n.summary||''} ${n.category} ${n.area} ${n.source}`.toLowerCase().includes(state.query.toLowerCase())))}

function renderBreaking(){
  const box=$('#breakingNews'); if(!box)return;
  const item=rankedNews().find(n=>n.isBreaking);
  if(!item){box.hidden=true;box.innerHTML='';return}
  box.hidden=false;
  const cov=coverage(item)>1?`<span class="breaking-coverage">${coverage(item)}媒体が掲載</span>`:'';
  box.innerHTML=`<span class="breaking-label">速報・重要</span><a href="${detailUrl(item)}"><strong>${esc(item.title)}</strong><small>${esc(item.area)}・${esc(item.source)}　${timeAgo(item.publishedAt)}</small></a>${cov}`;
}

function renderTopFive(){
  const items=rankedNews().slice(0,5);
  $('#topFive').innerHTML=items.map((n,i)=>{
    const cov=coverage(n)>1?`<span class="coverage-mini">${coverage(n)}媒体</span>`:'';
    const hot=n.isBreaking?'<span class="top-hot">重要</span>':'';
    return `<a class="top-five-item" href="${detailUrl(n)}"><span class="top-five-num">${i+1}</span><span class="top-five-copy"><strong>${esc(n.title)}</strong><small>${esc(n.source)} ${cov} ${hot}</small></span><time>${timeAgo(n.publishedAt)}</time></a>`
  }).join('')
}

function eventDateLabel(n){
  if(n.eventDateLabel)return String(n.eventDateLabel);
  const ranges=Array.isArray(n.eventDates)?n.eventDates:[];
  if(!ranges.length)return '';
  const fmtDate=s=>{const d=new Date(`${s}T00:00:00+09:00`);return `${d.getMonth()+1}月${d.getDate()}日`};
  return ranges.map(r=>r.start===r.end?fmtDate(r.start):`${fmtDate(r.start)}〜${fmtDate(r.end)}`).join('・');
}

function specialStoryMarkup(n,kind){
  const chip=kind==='openingClosing'?'開店・閉店':esc(n.area||'全市');
  const date=kind==='weekend'&&eventDateLabel(n)?`<span class="special-story-date">${esc(eventDateLabel(n))}</span>`:'';
  const sourceNote=kind==='weekend'&&n.eventDateSource?`<span class="special-story-source">日程：${esc(n.eventDateSource)}</span>`:'';
  return `<a class="special-story" href="${detailUrl(n)}"><div class="special-story-copy"><div class="special-story-topline"><span class="special-story-chip">${chip}</span>${date}</div><strong>${esc(n.title)}</strong><small>${esc(n.source)}　${timeAgo(n.publishedAt)} ${sourceNote}</small></div><span class="special-story-arrow">›</span></a>`;
}

function renderSpecialSections(){
  const openingsAll=sortedNews().filter(isOpeningClosing);
  const eventsAll=[...news].filter(isWeekendEvent).sort((a,b)=>score(b)-score(a)||new Date(b.publishedAt)-new Date(a.publishedAt));
  const openings=openingsAll.slice(0,5);
  const events=eventsAll.slice(0,5);
  const openingList=$('#openingClosingList'), weekendList=$('#weekendEventList');
  if(openingList){
    openingList.innerHTML=openings.length?openings.map(n=>specialStoryMarkup(n,'openingClosing')).join(''):'<div class="special-empty"><b>現在、該当情報はありません</b><span>新しい開店・閉店情報を自動収集中です。</span></div>';
    $('#openingClosingCount').textContent=`${openingsAll.length}件`;
    $('#showOpeningClosing').hidden=openingsAll.length===0;
  }
  if(weekendList){
    weekendList.innerHTML=events.length?events.map(n=>specialStoryMarkup(n,'weekend')).join(''):'<div class="special-empty"><b>今週末の該当イベントは未検出です</b><span>記事本文・公式ページ・市イベントカレンダー・観光サイトから開催日を確認しています。</span></div>';
    $('#weekendEventCount').textContent=`${eventsAll.length}件`;
    $('#showWeekendEvents').hidden=eventsAll.length===0;
  }
  if($('#weekendLabel'))$('#weekendLabel').textContent=weekendInfo?.label||'今週末';
}

function cardMarkup(n){
  const cov=coverage(n)>1?`<span class="coverage-badge">${coverage(n)}媒体が掲載</span>`:'';
  const breaking=n.isBreaking?'<span class="breaking-card-badge">速報</span>':'';
  const shop=isOpeningClosing(n)?'<span class="feature-badge shop-feature-badge">開店・閉店</span>':'';
  const weekend=isWeekendEvent(n)?'<span class="feature-badge weekend-feature-badge">今週末</span>':'';
  return `<article class="news-card"><div class="news-thumb" data-category="${esc(n.category)}"><span class="thumb-badge">${esc(n.category)}</span>${breaking}</div><div class="news-card-body"><div class="card-badges">${shop}${weekend}${cov}</div><h3><a href="${detailUrl(n)}">${esc(n.title)}</a></h3><p>${esc(n.summary||'')}</p><div class="news-card-footer"><div class="source"><b>${esc(n.area)}</b>${esc(n.source)}　${timeAgo(n.publishedAt)}</div><a class="read-button" href="${detailUrl(n)}" aria-label="記事詳細を開く">→</a></div></div></article>`
}
function renderNews(){const items=filteredNews();$('#newsGrid').innerHTML=items.map(cardMarkup).join('');$('#resultCount').textContent=`${items.length}件`;$('#emptyState').hidden=items.length!==0;updateRegionStates();renderFilterMode()}
function renderMeta(){if($('#sourceCount'))$('#sourceCount').textContent=sourceCount?`${sourceCount}媒体`:'複数媒体';if($('#lastUpdated'))$('#lastUpdated').textContent=generatedAt?`最終更新 ${fmt(generatedAt)}`:'デモデータ表示中'}
function renderFilterMode(){const el=$('#activeSpecialFilter');if(!el)return;if(!state.special){el.hidden=true;el.textContent='';return}el.hidden=false;el.textContent=state.special==='openingClosing'?'「開店・閉店」だけ表示中':'「今週末のイベント」だけ表示中'}
function regionCount(area){return news.filter(n=>n.area===area).length}
function regionButtonHtml(area){return `<button class="region-button" data-area="${esc(area)}" type="button" aria-label="${esc(area)}のニュースを見る"><span class="region-button-name">${esc(area)}</span><span class="region-button-count">${regionCount(area)}件</span></button>`}
function initRegionNav(){const nav=$('#regionNav');if(!nav)return;nav.innerHTML=`<button class="region-button region-all" data-area="すべて" type="button"><span class="region-button-name">市内全域</span><span class="region-button-count">${news.length}件</span></button>`+regionAreas.map(regionButtonHtml).join('');nav.addEventListener('click',e=>{const btn=e.target.closest('.region-button');if(btn)selectArea(btn.dataset.area)})}
function refreshRegionButtons(){const nav=$('#regionNav');if(!nav)return;nav.querySelectorAll('.region-button').forEach(btn=>{const area=btn.dataset.area;btn.classList.toggle('active',state.area===area);const count=area==='すべて'?news.length:regionCount(area);const countEl=btn.querySelector('.region-button-count');if(countEl)countEl.textContent=`${count}件`})}
function updateRegionStates(){refreshRegionButtons()}
function syncFilters(){if($('#categoryFilter'))$('#categoryFilter').value=state.category;if($('#areaFilter'))$('#areaFilter').value=state.area;if($('#searchInput'))$('#searchInput').value=state.query;if($('#headerSearchInput'))$('#headerSearchInput').value=state.query;$$('.nav-chip').forEach(b=>b.classList.toggle('active',b.dataset.category===state.category&&!state.special));$('#allFilter')?.classList.toggle('active',state.category==='すべて'&&state.area==='すべて'&&!state.query&&!state.special);renderNews()}
function selectArea(area){state.area=area;state.special=null;syncFilters();$('#latestTitle')?.scrollIntoView({behavior:'smooth',block:'start'})}
function selectCategory(cat){state.category=cat;state.special=null;syncFilters();$('#latestTitle')?.scrollIntoView({behavior:'smooth',block:'start'})}
function selectSpecial(kind){Object.assign(state,{category:'すべて',area:'すべて',query:'',sort:'new',special:kind});$('#sortFilter').value='new';syncFilters();$('#latestTitle')?.scrollIntoView({behavior:'smooth',block:'start'})}

async function loadNews(){
  try{
    const p=window.IWAKI_NOW_DEMO_DATA || await (async()=>{const r=await fetch('./data/news.json',{cache:'no-store'});if(!r.ok)throw new Error(r.status);return r.json()})();
    if(Array.isArray(p.news)&&p.news.length){news=p.news;generatedAt=p.generatedAt||null;sourceCount=p.sourceCount||(Array.isArray(p.sources)?p.sources.length:null);weekendInfo=p.weekend||null}
  }catch(e){console.info('内蔵デモデータを表示します',e)}
  renderBreaking();renderTopFive();renderSpecialSections();renderNews();renderMeta();refreshRegionButtons();
}

function bindEvents(){
  $$('.nav-chip').forEach(b=>b.addEventListener('click',()=>selectCategory(b.dataset.category)));
  $$('#mobileNav [data-category]').forEach(b=>b.addEventListener('click',()=>{selectCategory(b.dataset.category);$('#mobileNav').hidden=true}));
  $('#categoryFilter').addEventListener('change',e=>{state.category=e.target.value;state.special=null;syncFilters()});
  $('#areaFilter').addEventListener('change',e=>{state.area=e.target.value;state.special=null;syncFilters()});
  $('#sortFilter').addEventListener('change',e=>{state.sort=e.target.value;renderNews()});
  for(const id of ['searchInput','headerSearchInput'])$("#"+id).addEventListener('input',e=>{state.query=e.target.value;state.special=null;syncFilters()});
  $('#clearFilters').addEventListener('click',()=>{Object.assign(state,{category:'すべて',area:'すべて',query:'',sort:'new',special:null});$('#sortFilter').value='new';syncFilters()});
  $('#allFilter').addEventListener('click',()=>{Object.assign(state,{category:'すべて',area:'すべて',query:'',special:null});syncFilters()});
  $('#resetRegion').addEventListener('click',()=>{state.area='すべて';state.special=null;syncFilters()});
  $('#showAllNews').addEventListener('click',()=>$('#latestTitle').scrollIntoView({behavior:'smooth'}));
  $('#showOpeningClosing')?.addEventListener('click',()=>selectSpecial('openingClosing'));
  $('#showWeekendEvents')?.addEventListener('click',()=>selectSpecial('weekendEvents'));
  $('#menuButton').addEventListener('click',()=>{const nav=$('#mobileNav');nav.hidden=!nav.hidden;$('#menuButton').setAttribute('aria-expanded',String(!nav.hidden))});
}

bindEvents();initRegionNav();loadNews();
