/* DOM text is escaped before markup interpolation; all writes go through the API. */
'use strict';
let state = {"products": [], "movements": []};
const $ = selector => document.querySelector(selector);
const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const percent = value => `${(value * 100).toFixed(2)}%`;
const empty = message => `<p class="empty">${message}</p>`;
function notice(message, error = false) { $('#notice').textContent = message; $('#notice').className = message ? `notice${error ? ' error' : ''}` : ''; }
function stats(target, items) { $(target).innerHTML = items.map(([label, value]) => `<div class="stat"><small>${label}</small><strong>${value}</strong></div>`).join(''); }
let refreshSequence=0;
async function refresh() {
  const sequence=++refreshSequence;
  let received;
  try{received=await window.restTransport.send('/api/state',{method:'GET'});}
  catch(error){if(sequence!==refreshSequence)return;throw error;}
  if(sequence!==refreshSequence)return;
  const {response,text}=received;
  if (!response.ok) throw new Error('Could not load workspace. Check the server and refresh.');
  let next;try{next=JSON.parse(text);if(!next||!["products", "movements"].every(key=>Array.isArray(next[key])))throw Error();}catch{throw Error('The workspace returned an unreadable response. Check the server and refresh.');}
  const stockChanged = JSON.stringify([state.products,state.movements]) !== JSON.stringify([next.products,next.movements]);
  state = next; render();
  if (stockChanged) document.dispatchEvent(new Event('stock-updated'));
}

function visibleProducts(products,query,status,order){
  const search=query.trim().toLowerCase();
  const visible=products.filter(product=>(!search||`${product.name} ${product.sku}`.toLowerCase().includes(search))&&
    (status==='reorder'?product.stock<=product.reorder_point:status==='empty'?product.stock===0:
     status==='healthy'?product.stock>product.reorder_point:true));
  return visible.sort((a,b)=>{
    const difference=order==='stock'?a.stock-b.stock:order==='shortfall'?
      Math.max(0,b.reorder_point-b.stock)-Math.max(0,a.reorder_point-a.stock):0;
    return difference||a.name.localeCompare(b.name)||a.sku.localeCompare(b.sku);
  });
}
function renderInventoryOverview(){
  const query=$('#inventory-search').value,status=$('#inventory-status').value,order=$('#inventory-sort').value;
  const products=visibleProducts(state.products,query,status,order),custom=!!query.trim()||!!status||order!=='name';
  $('#products').innerHTML=products.map(p=>`<tr><td>${escape(p.name)}<small>${escape(p.sku)}</small></td><td>${p.stock}</td><td>${p.reorder_point}</td><td class="${p.stock<=p.reorder_point?'warning':''}">${p.stock<=p.reorder_point?'Reorder':'Healthy'}</td></tr>`).join('')||
    `<tr><td colspan="4">${state.products.length?'No products match these filters.':'No products yet. Add your first product below.'}</td></tr>`;
  $('#inventory-count').textContent=`${products.length} of ${state.products.length} products shown. Overview totals include all products.`;
  $('#inventory-view-options summary').textContent=custom?'Inventory filters · customized':'Filter inventory';
  $('#reset-inventory-filters').hidden=!custom;
}
function readInventoryFilters(){
  const params=new URLSearchParams(location.search);
  $('#inventory-search').value=(params.get('stock_q')||'').slice(0,200);
  $('#inventory-status').value=['reorder','empty','healthy'].includes(params.get('stock_status'))?params.get('stock_status'):'';
  $('#inventory-sort').value=['stock','shortfall'].includes(params.get('stock_sort'))?params.get('stock_sort'):'name';
}
function updateInventoryFilters(){
  const url=new URL(location.href);
  for(const [parameter,value] of [['stock_q',$('#inventory-search').value.trim()],['stock_status',$('#inventory-status').value],
                                 ['stock_sort',$('#inventory-sort').value==='name'?'':$('#inventory-sort').value]]){
    if(value)url.searchParams.set(parameter,value);else url.searchParams.delete(parameter);
  }
  history.replaceState(null,'',url);renderInventoryOverview();
}
$('#inventory-search').addEventListener('input',updateInventoryFilters);
$('#inventory-status').addEventListener('change',updateInventoryFilters);
$('#inventory-sort').addEventListener('change',updateInventoryFilters);
$('#reset-inventory-filters').addEventListener('click',()=>{
  $('#inventory-search').value='';$('#inventory-status').value='';$('#inventory-sort').value='name';
  updateInventoryFilters();$('#inventory-search').focus();
});
window.addEventListener('popstate',()=>{readInventoryFilters();renderInventoryOverview();});
readInventoryFilters();

function render() {
  stats('#inventory-stats', [['Products', state.products.length], ['Units on hand', state.products.reduce((n,p) => n+p.stock,0)], ['At reorder point', state.products.filter(p => p.stock <= p.reorder_point).length]]);
  renderInventoryOverview();
  const editing=$('#edit-product').value;
  $('#edit-product').innerHTML='<option value="">Select a product</option>'+state.products.map(p=>`<option value="${p.id}">${escape(p.name)} · ${escape(p.sku)}</option>`).join('');
  $('#edit-product').value=editing;
  const previous = $('#product-select').value;
  $('#product-select').innerHTML = '<option value="">Select a product</option>' + state.products.map(p => `<option value="${p.id}">${escape(p.name)} · ${p.stock} available</option>`).join('');
  $('#product-select').value = previous;
  $('#movements').innerHTML = state.movements.map(m => `<div class="activity"><strong>${m.delta > 0 ? '+' : ''}${m.delta}</strong> · ${escape(m.sku)} · ${escape(m.note)}<small>${escape(new Date(m.created_at).toLocaleString())}</small></div>`).join('') || empty('Stock movements will appear here.');
}

async function post(path, data) {
  let response,raw;
  try {
    const received=await window.restTransport.send(path,{method:'POST',headers:{'Content-Type':'application/json','X-Portfolio-Client':'browser'},body:JSON.stringify(data)});
    response=received.response;raw=received.text;
  } catch(error) {
    if(path==='/api/movements' && data.request_key)throw Error(`${error.message} The movement may have been saved. Keep the same inputs to retry with the same request key; it will not change stock twice.`);
    throw Error(window.restTransport.recoveryMessage(error,'POST'));
  }
  let result;try{result=JSON.parse(raw);if(!result||typeof result!=='object'||Array.isArray(result))throw Error();}catch{throw Error('The server returned an unreadable response. The write may have completed; refresh the workspace before retrying.');}
  if (!response.ok) {const error=new Error(result.error || 'Request failed');error.status=response.status;throw error;}
  return result;
}
function wireForm(id, path, numbers) {
  let pendingMovement = null;
  const feedback = document.createElement('p'); feedback.className = 'form-feedback'; feedback.setAttribute('role','status'); $(id).append(feedback);
  $(id).addEventListener('submit', async event => {
    event.preventDefault(); const form = event.currentTarget, button = form.querySelector('button');
    if (form.dataset.saving) return;
    const data = Object.fromEntries(new FormData(form)); numbers.forEach(key => data[key] = Number(data[key]));
    if (path === '/api/movements') {
      const signature = JSON.stringify(data);
      if (!pendingMovement || pendingMovement.signature !== signature) pendingMovement = {signature, key:crypto.randomUUID()};
      data.request_key = pendingMovement.key;
    }
    const controls = [...form.elements], disabled = controls.map(control => control.disabled);
    form.dataset.saving = 'true'; form.setAttribute('aria-busy','true');
    controls.forEach(control => control.disabled = true);
    const originalLabel = button.textContent;
    const feedback = form.querySelector('.form-feedback');
    feedback.textContent = 'Saving…';
    button.disabled = true; button.textContent = 'Saving…';
    try {
      const saved = await post(path,data); pendingMovement = null; form.reset();
      const message = saved.replayed ? 'Previously saved movement confirmed. Stock was not changed twice.' : 'Saved to your workspace.';
      try { await refresh(); notice(message); feedback.textContent = message; }
      catch(error) { const message = 'Saved successfully, but the workspace could not refresh. Reload to see the latest data.'; notice(message,true); feedback.textContent = message; }
    }
    catch(error) { notice(error.message,true); feedback.textContent = error.message; }
    finally { controls.forEach((control,i) => control.disabled = disabled[i]); button.textContent = originalLabel; delete form.dataset.saving; form.setAttribute('aria-busy','false'); }
  });
}

function loadProductEditor(){
 const form=$('#product-edit-form'),product=state.products.find(p=>String(p.id)===$('#edit-product').value);
 form.querySelector('[name=name]').value=product?.name||'';
 form.querySelector('[name=reorder_point]').value=product?.reorder_point??'';
 form.dataset.version=product?.version||'';
 form.querySelector('.product-feedback').textContent=product?`Loaded saved version ${product.version}.`:'';
}
$('#edit-product').addEventListener('change',loadProductEditor);
$('#reload-product').addEventListener('click',async()=>{
 const form=$('#product-edit-form'),feedback=form.querySelector('.product-feedback');
 if(form.dataset.saving)return;
 if(!$('#edit-product').value){feedback.textContent='Select a product first.';return;}
 const fields=[...form.elements];form.dataset.saving='true';fields.forEach(field=>field.disabled=true);
 try{await refresh();loadProductEditor();}
 catch(error){feedback.textContent='Could not load saved values. Your edits are preserved.';}
 finally{fields.forEach(field=>field.disabled=false);delete form.dataset.saving;}
});
$('#product-edit-form').addEventListener('submit',async event=>{
 event.preventDefault();const form=event.currentTarget;if(form.dataset.saving)return;
 const id=$('#edit-product').value,feedback=form.querySelector('.product-feedback');
 const data={name:form.querySelector('[name=name]').value,reorder_point:Number(form.querySelector('[name=reorder_point]').value),expected_version:Number(form.dataset.version)};
 const fields=[...form.elements];form.dataset.saving='true';form.setAttribute('aria-busy','true');fields.forEach(field=>field.disabled=true);feedback.textContent='Saving product details…';
 let saved=false;
 try {
  const result=await post(`/api/products/${id}/details`,data);saved=true;form.dataset.version=result.version;
  form.querySelector('[name=name]').value=result.name;form.querySelector('[name=reorder_point]').value=result.reorder_point;
  await refresh();feedback.textContent='Product details saved.';notice('Product details saved.');
 } catch(error){const message=saved?'Product details saved, but the overview could not refresh. Load saved values to refresh it.':error.message;feedback.textContent=message;notice(message,true);}
 finally{fields.forEach(field=>field.disabled=false);delete form.dataset.saving;form.setAttribute('aria-busy','false');}
});
wireForm('#product-form','/api/products',['reorder_point']);
wireForm('#movement-form','/api/movements',['product_id','delta']);

const titles = {inventory:"Stockroom"};
function navigate(page) {
  page = 'inventory';
  window.designIdentity(page);
  document.querySelectorAll('.page').forEach(section => section.hidden = section.id !== page);
  document.querySelectorAll('[data-page]').forEach(button => {button.classList.toggle('selected',button.dataset.page === page);button.setAttribute('aria-pressed',String(button.dataset.page === page));});
  $('#breadcrumb').textContent = `Workspace / ${titles[page]}`;
  document.title = `${titles[page]}`; notice('');
}
document.querySelectorAll('[data-page]').forEach(button => button.addEventListener('click', () => { location.hash = button.dataset.page; }));
window.addEventListener('hashchange', () => navigate(location.hash.slice(1)));
$('#export').addEventListener('click', () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(state,null,2)],{type:'application/json'}));
  const link = document.createElement('a'); link.href = url; link.download = 'stockroom-workspace.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
});
navigate(location.hash.slice(1));
refresh().catch(error => notice(error.message,true));
