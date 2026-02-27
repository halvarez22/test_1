document.addEventListener('DOMContentLoaded', () => {
    // --- Estado de la Aplicación ---
    // Guard: detectar y limpiar corrupción de localStorage
    (function cleanIfCorrupted() {
        try {
            const raw = localStorage.getItem('licitai_notebooks');
            if (!raw) return;
            const nbs = JSON.parse(raw);
            const corrupted = nbs.some(nb =>
                typeof nb.name === 'string' && (nb.name.trim().startsWith('{') || nb.name.trim().startsWith('['))
            );
            if (corrupted) {
                console.warn('⚠️ LocalStorage corrupto detectado. Limpiando y recargando desde DB...');
                localStorage.removeItem('licitai_notebooks');
            }
        } catch (e) {
            localStorage.removeItem('licitai_notebooks');
        }
    })();

    const safeStr = (v, def = '-') => {
        if (!v || v === 'N/D' || v === 'null') return def;
        if (typeof v === 'object') return Object.values(v).join(' ');
        return String(v);
    };

    let notebooks = JSON.parse(localStorage.getItem('licitai_notebooks')) || [];
    const normalizeName = (s) => (s || '').trim().toLowerCase();
    const dedupeNotebooksList = (list) => {
        const byName = new Map();
        for (const nb of list) {
            const key = normalizeName(nb.name);
            const existing = byName.get(key);
            if (!existing) {
                byName.set(key, nb);
            } else {
                const score = (Array.isArray(nb.sources) ? nb.sources.length : 0) + (nb.analysis ? 100 : 0) + (parseInt(nb.id, 10) || 0);
                const existingScore = (Array.isArray(existing.sources) ? existing.sources.length : 0) + (existing.analysis ? 100 : 0) + (parseInt(existing.id, 10) || 0);
                if (score >= existingScore) byName.set(key, nb);
            }
        }
        return Array.from(byName.values());
    };
    // Dedupe inicial por nombre (insensible a mayúsculas/minúsculas)
    if (notebooks.length > 1) {
        notebooks = dedupeNotebooksList(notebooks);
        try { localStorage.setItem('licitai_notebooks', JSON.stringify(notebooks)); } catch (e) { }
    }
    let activeNotebook = null;
    const fileStore = new Map(); // In-memory store for File objects (not persistable)

    // --- Selectores DOM ---
    const galleryView = document.getElementById('galleryView');
    const notebookView = document.getElementById('notebookView');
    const notebookGrid = document.getElementById('notebookGrid');
    const modalCreate = document.getElementById('modalCreate');

    // --- Navegación ---
    const showView = (viewId) => {
        document.querySelectorAll('.view').forEach(v => v.style.display = 'none');
        document.getElementById(viewId).style.display = 'flex';
    };

    const loadNotebooks = () => {
        const createBtn = document.getElementById('cardCreateNew');
        notebookGrid.innerHTML = '';
        notebookGrid.appendChild(createBtn);

        notebooks.forEach(nb => {
            const card = document.createElement('div');
            card.className = 'notebook-card';
            card.innerHTML = `
                <div class="card-top">
                    <span class="nb-icon">📒</span>
                    <button class="nb-menu-btn" data-id="${nb.id}" title="Opciones">⋮</button>
                </div>
                <h3>${nb.name}</h3>
                <div class="card-footer">
                    <span>${nb.sources.length} fuentes</span>
                    <span class="date">${nb.date}</span>
                </div>
            `;
            // Abrir cuaderno al hacer clic en la tarjeta (no en el botón de menú)
            card.onclick = (e) => {
                if (e.target.classList.contains('nb-menu-btn')) return;
                openNotebook(nb.id);
            };
            // Menú de opciones (eliminar)
            card.querySelector('.nb-menu-btn').onclick = (e) => {
                e.stopPropagation();
                showNotebookMenu(nb.id, nb.name, e.target);
            };
            notebookGrid.appendChild(card);
        });
    };

    const showNotebookMenu = (id, name, anchor) => {
        // Eliminar menú existente si hay uno abierto
        document.querySelectorAll('.nb-context-menu').forEach(m => m.remove());

        const menu = document.createElement('div');
        menu.className = 'nb-context-menu';
        menu.innerHTML = `
            <button class="menu-item danger" id="menuDelete">🗑️ Eliminar cuaderno</button>
        `;
        document.body.appendChild(menu);

        // Posicionar menú cerca del botón
        const rect = anchor.getBoundingClientRect();
        menu.style.top = `${rect.bottom + 8}px`;
        menu.style.left = `${rect.left - 140}px`;

        menu.querySelector('#menuDelete').onclick = () => {
            menu.remove();
            deleteNotebook(id, name);
        };

        // Cerrar al hacer clic fuera
        const closeMenu = (e) => {
            if (!menu.contains(e.target)) {
                menu.remove();
                document.removeEventListener('click', closeMenu);
            }
        };
        setTimeout(() => document.addEventListener('click', closeMenu), 100);
    };

    const deleteNotebook = async (id, name) => {
        if (!confirm(`¿Eliminar el cuaderno "${name}"? Esta acción no se puede deshacer.`)) return;

        try {
            const resp = await fetch(`http://localhost:8083/db/workspaces/${id}`, {
                method: 'DELETE'
            });
            if (!resp.ok) console.error("Error al eliminar en DB");
        } catch (e) {
            console.error("Error de conexión al eliminar", e);
        }

        if (activeNotebook && String(activeNotebook.id) === String(id)) {
            activeNotebook = null;
        }

        notebooks = notebooks.filter(n => n.id !== id);
        saveNotebooks(false); // Guardar en local sin intentar sync de un activeNotebook nulo/viejo
        loadNotebooks();
    };


    const openNotebook = async (id) => {
        activeNotebook = notebooks.find(n => n.id === id);
        if (!activeNotebook) return;

        document.getElementById('activeNotebookTitle').textContent = activeNotebook.name;
        renderSources();
        updateInfoBar();
        showView('notebookView');

        if (activeNotebook.taxData || activeNotebook.logoFilename) {
            updateEmpresaCard();
        }
        if (activeNotebook.analysis) {
            updateStudio(activeNotebook.analysis);
        }
        updateIntelStatusList();
        fetchGeneratedDocs();
        // Fetch inconsistencias para mostrar en la tarjeta (no bloquear render)
        try { fetchInconsistencias(); } catch (e) {}
        try {
            const resp = await fetch(`http://localhost:8083/db/workspaces/${id}`);
            if (resp.ok) {
                const ws = await resp.json();
                let taxData = null, actaData = null, analysis = null, logoFilename = null;
                try { taxData = ws.cif_data ? JSON.parse(ws.cif_data) : null; } catch (e) {}
                try { actaData = ws.acta_data ? JSON.parse(ws.acta_data) : null; } catch (e) {}
                try { analysis = ws.analysis ? JSON.parse(ws.analysis) : null; } catch (e) {}
                if (ws.logo_path) { logoFilename = ws.logo_path.split('/').pop(); }
                if (taxData) activeNotebook.taxData = { ...(activeNotebook.taxData || {}), ...taxData };
                if (actaData) activeNotebook.actaData = { ...(activeNotebook.actaData || {}), ...actaData };
                if (analysis) activeNotebook.analysis = analysis;
                if (logoFilename) activeNotebook.logoFilename = logoFilename;
                updateInfoBar();
                if (activeNotebook.taxData || activeNotebook.logoFilename) updateEmpresaCard();
                if (activeNotebook.analysis) updateStudio(activeNotebook.analysis);
                if (!activeNotebook.taxData) {
                    const cifSrc = (activeNotebook.sources || []).find(s => {
                        const n = (s.name || '').toLowerCase();
                        return n.includes('cif') || n.includes('situacion_fiscal') || n.includes('constancia');
                    });
                    if (cifSrc) {
                        await runAnalysis(cifSrc.id);
                    }
                }
                if (activeNotebook.analysis && !(activeNotebook.analysis.checklist_cumplimiento && activeNotebook.analysis.checklist_cumplimiento.length > 0)) {
                    try {
                        await fetch('http://localhost:8080/api/compliance/apply', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ workspace_id: String(id) })
                        });
                        const ws2 = await (await fetch(`http://localhost:8083/db/workspaces/${id}`)).json();
                        try { activeNotebook.analysis = ws2.analysis ? JSON.parse(ws2.analysis) : activeNotebook.analysis; } catch (e) {}
                        if (activeNotebook.analysis) updateStudio(activeNotebook.analysis);
                    } catch (e) {}
                }
            }
        } catch (e) {}
        initStudioCardInteractions();
    };


    // --- Resizing Lógica ---
    const initResizing = () => {
        const resizerSources = document.getElementById('resizerSources');
        const resizerStudio = document.getElementById('resizerStudio');
        const colSources = document.getElementById('colSources');
        const colChat = document.getElementById('colChat');
        const colStudio = document.getElementById('colStudio');

        const setupResizer = (resizer, leftCol, rightCol) => {
            let x = 0;
            let w = 0;

            const mouseDownHandler = (e) => {
                x = e.clientX;
                const styles = window.getComputedStyle(leftCol);
                w = parseInt(styles.width, 10);
                document.addEventListener('mousemove', mouseMoveHandler);
                document.addEventListener('mouseup', mouseUpHandler);
                resizer.classList.add('resizing');
            };

            const mouseMoveHandler = (e) => {
                const dx = e.clientX - x;
                leftCol.style.width = `${w + dx}px`;
            };

            const mouseUpHandler = () => {
                document.removeEventListener('mousemove', mouseMoveHandler);
                document.removeEventListener('mouseup', mouseUpHandler);
                resizer.classList.remove('resizing');
            };

            resizer.addEventListener('mousedown', mouseDownHandler);
        };

        setupResizer(resizerSources, colSources, colChat);
        // Para el de la derecha, el leftCol es colChat o colStudio? 
        // Usaremos setup invertido o simplemente redimensionar colStudio

        let x2 = 0;
        let w2 = 0;
        const mouseDownHandler2 = (e) => {
            x2 = e.clientX;
            const styles = window.getComputedStyle(colStudio);
            w2 = parseInt(styles.width, 10);
            document.addEventListener('mousemove', mouseMoveHandler2);
            document.addEventListener('mouseup', mouseUpHandler2);
            resizerStudio.classList.add('resizing');
        };
        const mouseMoveHandler2 = (e) => {
            const dx = x2 - e.clientX;
            colStudio.style.width = `${w2 + dx}px`;
        };
        const mouseUpHandler2 = () => {
            document.removeEventListener('mousemove', mouseMoveHandler2);
            document.removeEventListener('mouseup', mouseUpHandler2);
            resizerStudio.classList.remove('resizing');
        };
        resizerStudio.addEventListener('mousedown', mouseDownHandler2);
    };
    initResizing();


    // --- Gestión de Cuadernos ---
    document.getElementById('btnCreateNotebook').onclick = () => modalCreate.style.display = 'flex';
    document.getElementById('cardCreateNew').onclick = () => modalCreate.style.display = 'flex';
    document.getElementById('btnCancelCreate').onclick = () => modalCreate.style.display = 'none';

    document.getElementById('btnConfirmCreate').onclick = () => {
        const name = document.getElementById('newNotebookName').value;
        if (!name) return;

        const newNb = {
            id: String(Date.now()),
            name: name,
            sources: [],
            date: new Date().toLocaleDateString(),
            analysis: null
        };

        notebooks.push(newNb);
        saveNotebooks();
        loadNotebooks();
        modalCreate.style.display = 'none';
        document.getElementById('newNotebookName').value = '';
        openNotebook(newNb.id);
    };

    document.getElementById('btnBackToGallery').onclick = () => {
        activeNotebook = null;
        showView('galleryView');
    };

    const saveNotebooks = async (syncWithDB = true) => {
        localStorage.setItem('licitai_notebooks', JSON.stringify(notebooks));
        if (syncWithDB && activeNotebook) {
            try {
                // Reconstruir logo_path si hay logoFilename
                const logo_path = activeNotebook.logoFilename ? `/app/data/workspaces/${activeNotebook.id}/${activeNotebook.logoFilename}` : null;

                await fetch('http://localhost:8080/api/workspaces/sync', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        id: String(activeNotebook.id),
                        name: activeNotebook.name,
                        sources: JSON.stringify(activeNotebook.sources || []),
                        analysis: JSON.stringify(activeNotebook.analysis || {}),
                        cif_data: JSON.stringify(activeNotebook.taxData || {}),
                        logo_path: logo_path,
                        status: 'active'
                    })
                });
            } catch (e) { console.error("Sync failed", e); }
        }
    };

    const fetchNotebooks = async () => {
        try {
            const resp = await fetch('http://localhost:8083/db/workspaces');
            if (resp.ok) {
                const dbWs = await resp.json();
                // Actualizar siempre, incluso si está vacío (para permitir borrar el último)
                notebooks = dbWs.map(ws => {
                    const local = notebooks.find(n => String(n.id) === String(ws.id));

                    let sources = [];
                    try { sources = ws.sources ? JSON.parse(ws.sources) : (local ? local.sources : []); } catch (e) { }

                    let analysis = null;
                    try { analysis = ws.analysis ? JSON.parse(ws.analysis) : (local ? local.analysis : null); } catch (e) { }

                    let taxData = null;
                    try { taxData = ws.cif_data ? JSON.parse(ws.cif_data) : (local ? local.taxData : null); } catch (e) { }
                    let actaData = null;
                    try { actaData = ws.acta_data ? JSON.parse(ws.acta_data) : (local ? local.actaData : null); } catch (e) { }

                    // Extraer logo_filename del logo_path guardado
                    let logoFilename = null;
                    if (ws.logo_path) {
                        logoFilename = ws.logo_path.split('/').pop();
                    } else if (local && local.logoFilename) {
                        logoFilename = local.logoFilename;
                    }

                    return {
                        id: String(ws.id),
                        name: ws.name,
                        date: ws.date || (local ? local.date : new Date().toLocaleDateString()),
                        sources: sources,
                        analysis: analysis,
                        taxData: taxData,
                        actaData: actaData,
                        logoFilename: logoFilename
                    };
                });
                // Dedupe tras mezclar con DB (evita duplicados por nombre)
                notebooks = dedupeNotebooksList(notebooks);
                saveNotebooks(false); // Guardar en local sin re-sincronizar
                loadNotebooks();
            }
        } catch (e) { console.log("DB Load failed, using local", e); }
    };

    // --- Fuentes y Análisis ---
    const btnAddSource = document.getElementById('btnAddSource');
    const sourceUploadArea = document.getElementById('sourceUploadArea');
    const nbFileInput = document.getElementById('nbFileInput');

    const btnProcessAll = document.getElementById('btnProcessAll');

    btnAddSource.onclick = () => {
        sourceUploadArea.style.display = sourceUploadArea.style.display === 'none' ? 'block' : 'none';
        nbFileInput.click();
    };

    btnProcessAll.onclick = async () => {
        if (!activeNotebook) return;
        const pendings = activeNotebook.sources.filter(s => s.status === 'pending');
        for (const s of pendings) {
            await runAnalysis(s.id);
        }
    };

    nbFileInput.onchange = async (e) => {
        const files = Array.from(e.target.files);
        for (const file of files) {
            await addFileAsSource(file);
        }
        nbFileInput.value = ''; // Reset for next selection
    };

    const addFileAsSource = async (file) => {
        const fn = file.name.toLowerCase();
        const sourceId = `src-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;

        // Determine if it's analyzable
        const isPdf = fn.endsWith('.pdf');
        const isTxt = fn.endsWith('.txt');
        const isExcel = fn.endsWith('.xlsx') || fn.endsWith('.xls');
        const isImage = fn.endsWith('.jpg') || fn.endsWith('.jpeg') || fn.endsWith('.png');
        const isLogo = fn.includes('logo') || fn.includes('firma');

        let type = 'raw';
        if (isExcel) {
            // Archivos Excel → Propuesta Económica (DOCUMENTO E2)
            type = '/api/process-excel';
        } else if (isPdf || isTxt) {
            // Classify by filename keywords — works for both .pdf and .txt
            if (fn.includes('cif') || fn.includes('situacion_fiscal') || fn.includes('constancia')) {
                type = '/api/process-context?type=cif';
            } else if (fn.includes('acta') || fn.includes('escritura') || fn.includes('constitutiva') || fn.includes('notarial') || fn.includes('poder')) {
                type = '/api/process-context?type=acta';
            } else if (isTxt) {
                // A .txt file with no special keywords is treated as a base document
                type = '/api/analyze-base';
            } else {
                type = '/api/analyze-base';
            }
        } else if (isLogo || isImage) {
            type = '/api/process-context?type=logo';
        }

        const newSource = {
            id: sourceId,
            name: file.name,
            type: type,
            status: (type === 'raw') ? 'done' : 'pending',
            label: isExcel ? '📊 Cotización lista para procesar' : '📎 Archivo'
        };

        fileStore.set(sourceId, file);
        activeNotebook.sources.push(newSource);
        saveNotebooks();
        renderSources();
        // Procesamiento automático inmediato (evita tener que pulsar "Procesar")
        if (newSource.type && newSource.type !== 'raw') {
            try { await runAnalysis(sourceId); } catch (e) {}
        }
    };

    const runAnalysis = async (sourceId) => {
        const src = activeNotebook.sources.find(s => s.id === sourceId);
        let file = fileStore.get(sourceId);
        const wsId = String(activeNotebook.id);

        if (!src) return;

        // --- AUTO-RECLASIFICACIÓN ---
        // Si el archivo fue guardado con type='raw' (por la versión antigua del código),
        // lo reclasificamos ahora usando el nombre del archivo.
        if (!src.type || src.type === 'raw' || src.type === '/api/analyze-base') {
            const fn = src.name.toLowerCase();
            const isTxt = fn.endsWith('.txt');
            const isPdf = fn.endsWith('.pdf');
            const isExcel = fn.endsWith('.xlsx') || fn.endsWith('.xls');
            if (isExcel) {
                src.type = '/api/process-excel';
                src.status = 'pending';
                src.label = '📊 Cotización lista para procesar';
                saveNotebooks();
            } else if (isPdf || isTxt) {
                if (fn.includes('cif') || fn.includes('situacion_fiscal') || fn.includes('constancia')) {
                    src.type = '/api/process-context?type=cif';
                } else if (fn.includes('acta') || fn.includes('escritura') || fn.includes('constitutiva') || fn.includes('notarial') || fn.includes('poder')) {
                    src.type = '/api/process-context?type=acta';
                } else if (isTxt && src.type === 'raw') {
                    // .txt genérico → analizar como base
                    src.type = '/api/analyze-base';
                }
                saveNotebooks(); // Persistir la corrección
            }
        }

        // Si el tipo es todavía 'raw' no hay nada que analizar
        if (!src.type || src.type === 'raw') {
            addMessage(`ℹ️ El archivo "${src.name}" es solo de referencia y no requiere análisis.`, 'bot');
            return;
        }

        // Si no está en memoria, intentamos recuperarlo del servidor
        if (!file) {
            src.label = 'Recuperando archivo...';
            renderSources();
            try {
                const fileResp = await fetch(`http://localhost:8080/api/workspaces/${wsId}/download/${encodeURIComponent(src.name)}`);
                if (fileResp.ok) {
                    const blob = await fileResp.blob();
                    file = new File([blob], src.name, { type: blob.type });
                    fileStore.set(sourceId, file);
                } else {
                    alert('Archivo no encontrado en memoria ni en el servidor. Por favor vuelve a cargarlo.');
                    return;
                }
            } catch (e) {
                alert('Error al intentar recuperar el archivo del servidor.');
                return;
            }
        }

        src.status = 'loading';
        src.label = 'Iniciando...';
        renderSources();

        const formData = new FormData();
        formData.append('file', file);
        const endpoint = src.type;

        const sep = endpoint.includes('?') ? '&' : '?';
        const forceParam = (src.status === 'done') ? '&force=true' : '&force=false';
        try {
            // === FLUJO ESPECIAL: Excel → Propuesta Económica ===
            if (endpoint === '/api/process-excel') {
                src.label = '📊 Interpretando hoja de cálculo...';
                renderSources();
                addMessage(`📊 Procesando cotización <strong>${src.name}</strong>... Generando Propuesta Económica.`, 'bot');

                const resp = await fetch(`http://localhost:8080/api/process-excel?workspace_id=${wsId}`, {
                    method: 'POST',
                    body: formData
                });

                if (!resp.ok) {
                    const errText = await resp.text();
                    throw new Error(`Error generando propuesta económica: ${errText}`);
                }

                const contentType = resp.headers.get('content-type') || '';

                if (contentType.includes('application/vnd.openxmlformats') || contentType.includes('octet-stream')) {
                    // Respuesta binaria = Word generado exitosamente
                    const blob = await resp.blob();
                    const docFilename = 'DOCUMENTO_E2_Presupuesto.docx';

                    // Descargar automáticamente
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = docFilename;
                    document.body.appendChild(a);
                    a.click();
                    setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(url); }, 100);

                    src.status = 'done';
                    src.label = '✅ Propuesta Económica generada';

                    // Marcar precios como cargados
                    activeNotebook.excelProcessed = true;

                    addMessage(
                        `✅ <strong>¡Propuesta Económica generada!</strong><br>` +
                        `📝 Documento: <code style="background:rgba(255,255,255,0.08); padding:2px 6px; border-radius:4px;">${docFilename}</code><br>` +
                        `📊 Datos extraídos de: <em>${src.name}</em><br>` +
                        `El archivo se ha descargado y también está guardado en tu carpeta de proyecto.<br><br>` +
                        `<a href="http://localhost:8080/api/workspaces/${wsId}/download/${encodeURIComponent(docFilename)}" ` +
                        `target="_blank" style="display:inline-block; margin-top:4px; padding:6px 14px; background:linear-gradient(135deg,#22c55e,#16a34a); color:#fff; text-decoration:none; border-radius:8px; font-weight:600; font-size:13px;">` +
                        `📥 Descargar Propuesta Económica</a>`,
                        'bot'
                    );

                    updateInfoBar();
                    fetchGeneratedDocs();
                } else {
                    // La respuesta es JSON (error del servidor)
                    const data = await resp.json();
                    throw new Error(data.detail || data.msg || 'Error desconocido al procesar Excel');
                }

                saveNotebooks();
                renderSources();
                return;
            }

            // === FLUJO NORMAL: Análisis de documentos ===
                const resp = await fetch(`http://localhost:8080${endpoint}${sep}workspace_id=${wsId}${forceParam}`, {
                method: 'POST',
                body: formData
            });

            if (!resp.ok) throw new Error('Error en la conexión con el Agente');

            if (resp.headers.get('content-type').includes('application/x-ndjson')) {
                const reader = resp.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop();

                    for (const line of lines) {
                        if (!line.trim()) continue;
                        const chunk = JSON.parse(line);

                        if (chunk.status === 'info') {
                            src.label = chunk.msg;
                            addMessage(`ℹ️ ${chunk.msg}`, 'bot');
                        } else if (chunk.status === 'warning') {
                            src.label = 'OCR en proceso...';
                            addMessage(chunk.msg, 'bot');
                        } else if (chunk.status === 'progress') {
                            src.label = `${chunk.val}% - ${chunk.msg}`;
                        } else if (chunk.status === 'complete') {
                            src.status = 'done';

                            if (endpoint === '/api/analyze-base') {
                                activeNotebook.analysis = chunk.analysis;
                                if (chunk.profile && chunk.profile.licitante) {
                                    const l = chunk.profile.licitante;
                                    const t = activeNotebook.taxData || {};
                                    activeNotebook.taxData = {
                                        ...t,
                                        razon_social: (l.empresa && l.empresa !== 'N/D') ? l.empresa : (t.razon_social || 'N/D'),
                                        rfc: (l.rfc && l.rfc !== 'N/D' && l.rfc !== 'XAXX010101000') ? l.rfc : (t.rfc || 'XAXX010101000'),
                                        domicilio_fiscal: (l.domicilio && l.domicilio !== 'N/D' && l.domicilio !== 'DOMICILIO') ? l.domicilio : (t.domicilio_fiscal || 'N/D'),
                                        representante_legal: (l.representante && l.representante !== 'N/D' && l.representante !== '-' && l.representante !== 'REPRESENTANTE') ? l.representante : (t.representante_legal || 'N/D'),
                                        cargo: (l.cargo && l.cargo !== 'Representante Legal' && l.cargo !== 'N/D') ? l.cargo : (t.cargo || 'Representante Legal')
                                    };
                                }
                                updateInfoBar();
                                const licitId = chunk.analysis.numero_licitacion || 'N/D';
                                addMessage(`✅ Análisis finalizado: <strong>${licitId}</strong>`, 'bot');

                                if (chunk.generated_docs && chunk.generated_docs.length > 0) {
                                    const docsHtml = chunk.generated_docs.map(d => `<li>📄 ${d}</li>`).join('');
                                    addMessage(`✍️ <strong>Generador Agent:</strong> He redactado los siguientes documentos basados en las bases:<br><ul>${docsHtml}</ul><br>Los encontrarás en tu carpeta de proyecto.`, 'bot');
                                }
                            // Aplicar Compliance automáticamente si no existe checklist
                            try {
                                const hasChecklist = Array.isArray(activeNotebook.analysis?.checklist_cumplimiento) && activeNotebook.analysis.checklist_cumplimiento.length > 0;
                                if (!hasChecklist) {
                                    await fetch('http://localhost:8080/api/compliance/apply', {
                                        method: 'POST',
                                        headers: { 'Content-Type': 'application/json' },
                                        body: JSON.stringify({ workspace_id: wsId })
                                    });
                                    const ws2 = await (await fetch(`http://localhost:8083/db/workspaces/${wsId}`)).json();
                                    try { activeNotebook.analysis = ws2.analysis ? JSON.parse(ws2.analysis) : activeNotebook.analysis; } catch (e) {}
                                    if (activeNotebook.analysis) updateStudio(activeNotebook.analysis);
                                }
                            } catch (e) {}
                            } else if (endpoint.includes('type=cif')) {
                                activeNotebook.taxData = { ...activeNotebook.taxData, ...chunk.data };
                                updateInfoBar();
                                addMessage(`✅ Datos fiscales extraídos correctamente.`, 'bot');
                            } else if (endpoint.includes('type=acta')) {
                                if (chunk.data && chunk.data.representante) {
                                    activeNotebook.taxData = activeNotebook.taxData || {};
                                    activeNotebook.taxData.representante_legal = chunk.data.representante;
                                    activeNotebook.taxData.cargo = chunk.data.cargo;
                                }
                                updateInfoBar();
                                addMessage(`✅ Acta Constitutiva procesada: se identificó a <strong>${chunk.data.representante || 'el representante'}</strong> como apoderado.`, 'bot');
                            } else if (endpoint === '/api/process-context') {
                                addMessage(`✅ Archivo de contexto (${src.name}) procesado correctamente.`, 'bot');
                            }
                        } else if (chunk.status === 'error') {
                            throw new Error(chunk.msg);
                        }
                        renderSources();
                    }
                }
            } else {
                // Not a stream (for Logo/CIF simplifications)
                const data = await resp.json();
                src.status = 'done';
                if (data && data.status === 'success') {
                    addMessage(`✅ ${data.msg || 'Archivo procesado'}`, 'bot');

                    // --- CIF: guardar datos y mostrar tarjeta empresa ---
                    if (endpoint.includes('cif') || (endpoint.includes('context') && src.type && src.type.includes('cif'))) {
                        if (data.data) {
                            // Normalizar campos para que coincidan con ProfileAgent
                            const d = data.data;
                            activeNotebook.taxData = {
                                ...activeNotebook.taxData,
                                razon_social: d.razon_social,
                                rfc: d.rfc,
                                representante_legal: d.representante || d.representante_legal,
                                domicilio_fiscal: d.domicilio || d.domicilio_fiscal,
                                tipo_persona: d.tipo_persona,
                                regimen_fiscal: d.regimen_fiscal
                            };
                            saveNotebooks();
                            updateEmpresaCard();
                            addMessage(
                                `🏛️ <strong>Empresa detectada:</strong> ${d.razon_social || '-'}<br>` +
                                `📋 RFC: <code style="background:rgba(255,255,255,0.08); padding:1px 5px; border-radius:3px;">${d.rfc || '-'}</code><br>` +
                                `📍 Domicilio: ${String(d.domicilio || d.domicilio_fiscal || '-').substring(0, 60)}...`,
                                'bot'
                            );
                            try { await fetchInconsistencias(); } catch (e) {}
                        }
                    }

                    // --- Logo: guardar filename y mostrar preview ---
                    if (endpoint.includes('logo') || (endpoint.includes('context') && src.type && src.type.includes('logo'))) {
                        if (data.logo_filename) {
                            activeNotebook.logoFilename = data.logo_filename;
                            saveNotebooks();
                            updateEmpresaCard();
                            addMessage(`🖼️ Logo corporativo cargado correctamente.`, 'bot');
                        }
                    }

                    // --- Acta: guardar representante y cargo ---
                    if (endpoint.includes('acta') || (endpoint.includes('context') && src.type && src.type.includes('acta'))) {
                        if (data.data) {
                            activeNotebook.actaData = data.data;
                            activeNotebook.taxData = activeNotebook.taxData || {};
                            activeNotebook.taxData.cargo = data.data.cargo || activeNotebook.taxData.cargo;
                            saveNotebooks();
                            updateEmpresaCard();
                            addMessage(`📜 <strong>Acta Constitutiva:</strong> Identificado a ${data.data.representante || 'el representante'} como apoderado.`, 'bot');
                            try { await fetchInconsistencias(); } catch (e) {}
                        }
                    }

                    updateInfoBar();
                    fetchGeneratedDocs();
                } else if (data) {
                    addMessage(`ℹ️ ${data.msg || 'Procesado con observaciones'}`, 'bot');
                }
                renderSources();

            }

            saveNotebooks();
        } catch (err) {
            console.error(err);
            src.status = 'error';
            src.error = err.message;
            saveNotebooks();
            renderSources();
            addMessage(`❌ Error en "<strong>${src.name}</strong>": ${err.message}`, 'bot');
        }
    };


    const deleteSource = (sourceId) => {
        if (!activeNotebook) return;
        activeNotebook.sources = activeNotebook.sources.filter(s => s.id !== sourceId);
        if (typeof fileStore !== 'undefined') fileStore.delete(sourceId);
        saveNotebooks();
        renderSources();
    };

    const renderSources = () => {
        const list = document.getElementById('sourceList');
        if (activeNotebook.sources.length === 0) {
            list.innerHTML = '<p class="empty-msg">No hay fuentes cargadas</p>';
            return;
        }
        const icons = {
            'raw': '📎',
            '/api/analyze-base': '📄',
            '/api/process-cif': '🏛️',
            '/api/process-acta': '📜',
            '/api/process-excel': '📊',
            '/api/process-context?type=cif': '🏛️',
            '/api/process-context?type=acta': '📜',
            '/api/process-context?type=logo': '🖼️',
            'error': '📄'
        };
        list.innerHTML = activeNotebook.sources.map(s => {
            let statusHtml = '';
            if (s.status === 'loading') {
                statusHtml = `<div class="source-status"><span class="spinner"></span><span class="status-label">${s.label || 'Procesando...'}</span></div>`;
            } else if (s.status === 'done') {
                statusHtml = `<div class="source-status done">✅ Listo <button class="btn-ghost-mini" style="font-size:10px; padding: 0 4px;" onclick="event.stopPropagation(); runAnalysis('${s.id}')" title="Re-analizar">↻</button></div>`;
            } else if (s.status === 'error') {
                statusHtml = `<div class="source-status error" title="${s.error || ''}">❌ Error <button class="btn-mini-play" onclick="event.stopPropagation(); runAnalysis('${s.id}')">↻</button></div>`;
            } else if (s.status === 'pending') {
                statusHtml = `<div class="source-status pending"><button class="btn-mini-play" onclick="event.stopPropagation(); runAnalysis('${s.id}')">▶ Analizar</button></div>`;
            }
            return `
            <div class="source-item ${s.status || ''}" data-id="${s.id}">
                <span class="icon">${icons[s.type] || '📄'}</span>
                <div class="source-info">
                    <span class="name">${s.name}</span>
                    ${statusHtml}
                </div>
                <button class="btn-delete-source" onclick="event.stopPropagation(); deleteSource('${s.id}')" title="Eliminar fuente">×</button>
            </div>`;
        }).join('');

        window.runAnalysis = runAnalysis;
        window.deleteSource = deleteSource;
    };


    const updateEmpresaCard = () => {
        const card = document.getElementById('cardEmpresa');
        const nb = activeNotebook;
        const cif = nb.taxData || {};
        const acta = nb.actaData || {};
        const wsId = String(nb.id);

        if (cif.razon_social || nb.logoFilename) {
            card.style.display = 'block';
        }

        document.getElementById('empresaNombre').textContent = safeStr(cif.razon_social, 'Empresa');
        document.getElementById('empresaRFC').textContent = `RFC: ${safeStr(cif.rfc)}`;
        const repEl = document.getElementById('empresaRep');
        const repContainer = repEl.parentElement;
        const repVal = safeStr(acta.representante_legal || acta.representante || '');
        if (repVal && repVal !== '-') {
            repEl.textContent = repVal;
            repContainer.style.display = 'block';
        } else {
            repEl.textContent = '';
            repContainer.style.display = 'none';
        }
        const domEl = document.getElementById('empresaDom');
        domEl.textContent = safeStr(cif.domicilio_fiscal || cif.domicilio);
        domEl.title = safeStr(cif.domicilio_fiscal || cif.domicilio);

        // Logo
        const logoImg = document.getElementById('empresaLogo');
        if (nb.logoFilename) {
            const logoUrl = `http://localhost:8080/api/workspaces/${wsId}/download/${encodeURIComponent(nb.logoFilename)}`;
            logoImg.src = logoUrl;
            logoImg.style.display = 'block';
        } else {
            logoImg.style.display = 'none';
        }
    };

    const updateStudio = (analysis) => {
        if (!analysis) return;

        // --- Puntos Críticos (Sine Qua Non) ---
        const critical = analysis.puntos_criticos || {};
        const warnings = [];

        if (critical.dirigido_a && critical.dirigido_a !== 'N/D') {
            warnings.push(`<div class="warning-item"><span class="critical-label">DIRIGIR A:</span> ${critical.dirigido_a}</div>`);
        }
        if (critical.firma_requerida && critical.firma_requerida !== 'N/D') {
            warnings.push(`<div class="warning-item"><span class="critical-label">FIRMA:</span> ${critical.firma_requerida}</div>`);
        }
        if (critical.lugar_entrega && critical.lugar_entrega !== 'N/D') {
            warnings.push(`<div class="warning-item"><span class="critical-label">ENTREGA EN:</span> ${critical.lugar_entrega}</div>`);
        }
        if (critical.advertencias && Array.isArray(critical.advertencias)) {
            critical.advertencias.forEach(adv => {
                if (adv && adv !== 'N/D') warnings.push(`<div class="warning-item">${adv}</div>`);
            });
        }

        const criticalCard = document.getElementById('cardCriticalRules');
        const warningsList = document.getElementById('criticalWarningsList');

        if (warnings.length > 0) {
            warningsList.innerHTML = warnings.join('');
            criticalCard.style.display = 'block';
            const wsId = String(activeNotebook.id);
            fetch('http://localhost:8080/api/critical-rules/recompute', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ workspace_id: wsId })
            }).then(r => r.json()).then(recomp => {
                try {
                    if (recomp && recomp.status === 'success' && recomp.data && recomp.data.puntos_criticos) {
                        analysis.puntos_criticos = recomp.data.puntos_criticos;
                        activeNotebook.analysis = analysis;
                        const c2 = analysis.puntos_criticos || {};
                        const w2 = [];
                        if (c2.dirigido_a && c2.dirigido_a !== 'N/D') {
                            w2.push(`<div class="warning-item"><span class="critical-label">DIRIGIR A:</span> ${c2.dirigido_a}</div>`);
                        }
                        if (c2.firma_requerida && c2.firma_requerida !== 'N/D') {
                            w2.push(`<div class="warning-item"><span class="critical-label">FIRMA:</span> ${c2.firma_requerida}</div>`);
                        }
                        if (c2.lugar_entrega) {
                            w2.push(`<div class="warning-item"><span class="critical-label">ENTREGA EN:</span> ${c2.lugar_entrega || ''}</div>`);
                        }
                        if (c2.advertencias && Array.isArray(c2.advertencias)) {
                            c2.advertencias.forEach(adv => { if (adv && adv !== 'N/D') w2.push(`<div class="warning-item">${adv}</div>`); });
                        }
                        warningsList.innerHTML = w2.join('');
                    }
                } catch (e) {}
                return fetch('http://localhost:8080/api/critical-rules/evidence', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ workspace_id: wsId })
                });
            }).then(r => r.json()).then(ev => {
                const e = ev && ev.data ? ev.data : {};
                const c3 = (activeNotebook.analysis || {}).puntos_criticos || {};
                const bullets = [];
                if (c3.dirigido_a && c3.dirigido_a !== 'N/D') {
                    const evDir = e.dirigido_a && e.dirigido_a.found ? `<div style="font-size:11px;color:#a5b4fc;">Evidencia: ${e.dirigido_a.evidence.file} — ${e.dirigido_a.evidence.snippet}</div>` : `<div style="font-size:11px;color:#fca5a5;">Sin evidencia textual</div>`;
                    bullets.push(`<div class="warning-item"><span class="critical-label">DIRIGIR A:</span> ${c3.dirigido_a}${evDir}</div>`);
                }
                if (c3.firma_requerida && c3.firma_requerida !== 'N/D') {
                    const evFir = e.firma_requerida && e.firma_requerida.found ? `<div style="font-size:11px;color:#a5b4fc;">Evidencia: ${e.firma_requerida.evidence.file} — ${e.firma_requerida.evidence.snippet}</div>` : `<div style="font-size:11px;color:#fca5a5;">Sin evidencia textual</div>`;
                    bullets.push(`<div class="warning-item"><span class="critical-label">FIRMA:</span> ${c3.firma_requerida}${evFir}</div>`);
                }
                if (c3.lugar_entrega) {
                    const evLug = e.lugar_entrega && e.lugar_entrega.found ? `<div style="font-size:11px;color:#a5b4fc;">Evidencia: ${e.lugar_entrega.evidence.file} — ${e.lugar_entrega.evidence.snippet}</div>` : `<div style="font-size:11px;color:#fca5a5;">Sin evidencia textual</div>`;
                    bullets.push(`<div class="warning-item"><span class="critical-label">ENTREGA EN:</span> ${c3.lugar_entrega}${evLug}</div>`);
                }
                if (c3.advertencias && Array.isArray(c3.advertencias)) {
                    c3.advertencias.forEach(adv => {
                        if (adv && adv !== 'N/D') {
                            const res = (e.advertencias || []).find(x => x.text === adv);
                            const evAdv = res && res.found ? `<div style="font-size:11px;color:#a5b4fc;">Evidencia: ${res.evidence.file} — ${res.evidence.snippet}</div>` : `<div style="font-size:11px;color:#fca5a5;">Sin evidencia textual</div>`;
                            bullets.push(`<div class="warning-item">${adv}${evAdv}</div>`);
                        }
                    });
                }
                warningsList.innerHTML = bullets.join('');
            }).catch(() => {});
        } else {
            criticalCard.style.display = 'none';
        }

        // --- Plan Maestro de Auditoría (Interactiva y Semántica) ---
        const checklist = analysis.checklist_cumplimiento || [];
        const auditCard = document.getElementById('cardComplianceAudit');
        const auditList = document.getElementById('complianceChecklist');

        // Estado persistente de checks (si no existe, lo inicializamos)
        if (!activeNotebook.auditState) activeNotebook.auditState = {};

        if (checklist.length > 0) {
            auditList.innerHTML = checklist.map((item, idx) => {
                const itemId = `audit_${idx}`;
                const isChecked = activeNotebook.auditState[itemId] || false;

                // Lógica de "Verified" (Pre-Auditoría Automática)
                // Buscamos si el punto del checklist coincide con alguna fuente subida (ej. SAT, IMSS)
                const sources = activeNotebook.sources || [];
                const isVerified = sources.some(s =>
                    s.status === 'done' && (
                        (item.punto.toLowerCase().includes('sat') && s.name.toLowerCase().includes('sat')) ||
                        (item.punto.toLowerCase().includes('imss') && s.name.toLowerCase().includes('imss')) ||
                        (item.punto.toLowerCase().includes('infonavit') && s.name.toLowerCase().includes('infonavit')) ||
                        (item.punto.toLowerCase().includes('acta') && s.name.toLowerCase().includes('acta'))
                    ));

                return `
                    <div class="audit-item ${isChecked ? 'completed' : ''}" data-id="${itemId}">
                        <div style="display:flex; align-items:flex-start; gap:10px;">
                            <input type="checkbox" class="audit-chk" ${isChecked ? 'checked' : ''} data-id="${itemId}">
                            <div style="flex:1;">
                                <div class="audit-point">
                                    ${item.punto}
                                    ${isVerified ? '<span class="verified-badge" title="Evidencia detectada en archivos">✅ Verificado</span>' : ''}
                                </div>
                                <div class="audit-reason">${item.motivo_riesgo}</div>
                                <div class="audit-action">${item.accion_preventiva}</div>
                            </div>
                        </div>
                    </div>
                `;
            }).join('');

            // Agregar listeners a los nuevos checkboxes
            auditList.querySelectorAll('.audit-chk').forEach(chk => {
                chk.addEventListener('change', (e) => {
                    const id = e.target.dataset.id;
                    activeNotebook.auditState[id] = e.target.checked;
                    e.target.closest('.audit-item').classList.toggle('completed', e.target.checked);
                    saveNotebooks();
                });
            });

            auditCard.style.display = 'block';
        } else {
            auditCard.style.display = 'none';
        }

        // --- Fianzas ---
        const fianzas = analysis.fianzas_requeridas;
        let fianzasTexto = 'No detectado';
        if (typeof fianzas === 'object' && fianzas) {
            const parts = [];
            if (fianzas.garantia_seriedad) parts.push(`Seriedad: ${fianzas.garantia_seriedad}`);
            if (fianzas.cumplimiento) parts.push(`Cumplimiento: ${fianzas.cumplimiento}`);
            fianzasTexto = parts.join(' | ') || 'No detectado';
        } else if (typeof fianzas === 'string') {
            fianzasTexto = fianzas;
        }
        document.querySelector('#cardFianzas .card-val').textContent = fianzasTexto;

        // --- Formatos / Anexos ---
        const catAnexos = analysis.categorized_anexos || {};
        const oldAnexos = analysis.anexos_requeridos || analysis.documentos_requeridos || [];
        const allAnexos = [...(catAnexos.technical || []), ...(catAnexos.economic || []), ...(Array.isArray(oldAnexos) ? oldAnexos : [])];
        const totalAnexos = allAnexos.length;
        const formatosEl = document.querySelector('#cardFormatos .card-val');
        formatosEl.textContent = totalAnexos > 0
            ? `${totalAnexos} documentos detectados`
            : 'No detectados';
        if (totalAnexos > 0) {
            formatosEl.title = allAnexos.join('\n');
        }

        // --- Certificaciones ---
        document.querySelector('#cardCertificaciones .card-val').textContent = analysis.certificaciones_y_normas || analysis.certificaciones || 'No detectado';

        // --- Fechas Clave ---
        const dates = analysis.fechas_clave || analysis.fechas || {};
        const dateList = document.querySelector('#cardFechas .date-list');
        const dateItems = [
            dates.visita ? `<li>Visita:      ${dates.visita}</li>` : '',
            dates.aclaraciones ? `<li>Junta:       ${dates.aclaraciones}</li>` : '',
            dates.apertura ? `<li>Apertura:    ${dates.apertura}</li>` : '',
            dates.fallo ? `<li>Fallo:       ${dates.fallo}</li>` : '',
        ].filter(Boolean);
        dateList.innerHTML = dateItems.length > 0 ? dateItems.join('') : '<li>No detectadas</li>';
    };

    const fetchInconsistencias = async () => {
        if (!activeNotebook) return;
        const wsId = String(activeNotebook.id);
        try {
            const resp = await fetch(`http://localhost:8080/api/workspaces/${wsId}/inconsistencias`);
            if (!resp.ok) return;
            const payload = await resp.json();
            const data = payload.data || {};
            activeNotebook.inconsistenciasData = data;
            const card = document.getElementById('cardInconsistencias');
            const listEl = document.getElementById('inconsistenciasList');
            const incs = Array.isArray(data.inconsistencias) ? data.inconsistencias : [];
            if (incs.length === 0) {
                listEl.innerHTML = '<p class="empty-msg-mini">Sin inconsistencias</p>';
                card.style.display = 'none';
                return;
            }
            const items = incs.map(i => {
                const sev = i.severidad || 'baja';
                const tipo = i.tipo || 'identidad';
                const det = i.detalle || '';
                const rec = i.recomendacion || '';
                return `<div class="warning-item"><span class="critical-label">${tipo.toUpperCase()} (${sev})</span> ${det}<br><em>${rec}</em></div>`;
            });
            listEl.innerHTML = items.join('');
            card.style.display = 'block';
        } catch (e) { /* ignore */ }
    };

    // NER desactivado (interno): no se realiza carga automática ni UI

    const renderInconsistenciasDetails = () => {
        const data = activeNotebook && activeNotebook.inconsistenciasData ? activeNotebook.inconsistenciasData : {};
        const cmp = data.comparacion || {};
        const incs = Array.isArray(data.inconsistencias) ? data.inconsistencias : [];
        const hdr = `<div style="font-weight:600; color:var(--text-primary); margin-bottom:6px;">Detalle técnico</div>`;
        const cmpHtml = `
            <div style="display:grid; grid-template-columns: 1fr 1fr; gap:8px; margin-bottom:8px;">
                <div>
                    <div style="font-size:10px; color:var(--accent);">CIF</div>
                    <div>RFC: <code>${safeStr(cmp.rfc_cif)}</code></div>
                    <div>Razón: ${safeStr(cmp.razon_social_cif)}</div>
                </div>
                <div>
                    <div style="font-size:10px; color:var(--accent);">Acta</div>
                    <div>RFC: <code>${safeStr(cmp.rfc_acta)}</code></div>
                    <div>Razón: ${safeStr(cmp.razon_social_acta)}</div>
                </div>
            </div>
            <div>Similitud de razón social: <strong>${cmp.similitud_razon_social !== undefined ? Number(cmp.similitud_razon_social).toFixed(2) : '-'}</strong></div>
            <div>Representante en Acta: ${safeStr(cmp.representante_acta)}</div>
            <div>Cargo en Acta: ${safeStr(cmp.cargo_acta)}</div>
        `;
        const listHtml = incs.map(i => {
            const sev = i.severidad || 'baja';
            const tipo = i.tipo || 'identidad';
            const det = i.detalle || '';
            const rec = i.recomendacion || '';
            return `<div style="margin-top:8px;"><div style="font-weight:600;">${tipo.toUpperCase()} (${sev})</div><div>${det}</div><div style="color:#a5b4fc;">${rec}</div></div>`;
        }).join('');
        return hdr + cmpHtml + listHtml;
    };

    const initStudioCardInteractions = () => {
        const overlay = document.getElementById('cardOverlay');
        const overlayTitle = document.getElementById('cardOverlayTitle');
        const overlayBody = document.getElementById('cardOverlayBody');
        const overlayClose = document.getElementById('cardOverlayClose');
        const openOverlay = (title, html) => {
            overlayTitle.textContent = title;
            overlayBody.innerHTML = html;
            overlay.style.display = 'flex';
            document.body.style.overflow = 'hidden';
        };
        const closeOverlay = () => { overlay.style.display = 'none'; overlayBody.innerHTML = ''; document.body.style.overflow = ''; };
        if (overlayClose) overlayClose.onclick = closeOverlay;
        overlay.addEventListener('click', (e) => { if (e.target.id === 'cardOverlay') closeOverlay(); });

        document.querySelectorAll('.studio-card').forEach(card => {
            card.addEventListener('mouseenter', () => {
                card.style.boxShadow = '0 6px 18px rgba(99,102,241,0.25)';
                card.style.transform = 'translateY(-2px)';
                card.style.transition = 'all 0.15s ease';
            });
            card.addEventListener('mouseleave', () => {
                card.style.boxShadow = 'none';
                card.style.transform = 'none';
            });
            card.addEventListener('click', () => {
                const isInc = card.id === 'cardInconsistencias';
                if (isInc) {
                    const html = renderInconsistenciasDetails();
                    openOverlay('Inconsistencias', html);
                } else {
                    const isCritical = card.id === 'cardCriticalRules';
                    if (isCritical) {
                        const wsId = String(activeNotebook.id);
                        openOverlay('Reglas SINE QUA NON', 'Verificando evidencias...');
                        const analysis = activeNotebook.analysis || {};
                        const crit = analysis.puntos_criticos || {};
                        const dirigido_a = crit.dirigido_a || 'N/D';
                        const firma = crit.firma_requerida || 'N/D';
                        const lugar = crit.lugar_entrega || 'N/D';
                        const advList = Array.isArray(crit.advertencias) ? crit.advertencias : (crit.advertencias ? [crit.advertencias] : []);
                        fetch('http://localhost:8080/api/critical-rules/evidence', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ workspace_id: wsId })
                        }).then(r => r.json()).then(ev => {
                            const e = ev && ev.data ? ev.data : {};
                            const bullets = [];
                            bullets.push(`Debes dirigir la propuesta técnica y económica a: <strong>${dirigido_a}</strong>.` + (e.dirigido_a && e.dirigido_a.found && e.dirigido_a.evidence ? `<div style="font-size:11px;color:#a5b4fc;">Evidencia: ${e.dirigido_a.evidence.file} — ${e.dirigido_a.evidence.snippet}</div>` : ''));
                            bullets.push(`Recuerda firmar: <strong>${firma}</strong>.` + (e.firma_requerida && e.firma_requerida.found && e.firma_requerida.evidence ? `<div style="font-size:11px;color:#a5b4fc;">Evidencia: ${e.firma_requerida.evidence.file} — ${e.firma_requerida.evidence.snippet}</div>` : ''));
                            bullets.push(`Entrega física en: <strong>${lugar}</strong>.` + (e.lugar_entrega && e.lugar_entrega.found && e.lugar_entrega.evidence ? `<div style="font-size:11px;color:#a5b4fc;">Evidencia: ${e.lugar_entrega.evidence.file} — ${e.lugar_entrega.evidence.snippet}</div>` : ''));
                            if (advList.length > 0) {
                                advList.forEach(a => {
                                    const evItem = (e.advertencias || []).find(x => x.text === a) || {};
                                    bullets.push(`Importante: ${a}.` + (evItem && evItem.found && evItem.evidence ? `<div style="font-size:11px;color:#a5b4fc;">Evidencia: ${evItem.evidence.file} — ${evItem.evidence.snippet}</div>` : ''));
                                });
                            }
                            overlayBody.innerHTML = bullets.map(b => `• ${b}`).join('<br>');
                        }).catch(() => {
                            const bullets = [
                                `Debes dirigir la propuesta técnica y económica a: <strong>${dirigido_a}</strong>.`,
                                `Recuerda firmar: <strong>${firma}</strong>.`,
                                `Entrega física en: <strong>${lugar}</strong>.`,
                                ...advList.map(a => `Importante: ${a}.`)
                            ];
                            overlayBody.innerHTML = bullets.map(b => `• ${b}`).join('<br>');
                        });
                        return;
                    }
                    const title = card.querySelector('h4') ? card.querySelector('h4').textContent : 'Detalle';
                    const content = card.innerHTML;
                    openOverlay(title, content);
                }
            });
        });
    };

    // renderNERChips eliminado: UI de NER retirada

    const updateInfoBar = () => {
        const analysis = activeNotebook.analysis || {};
        const tax = activeNotebook.taxData || {};

        // Convocante con truncado inteligente
        document.getElementById('infoConvocante').textContent = safeStr(analysis.convocante);
        document.getElementById('infoLicitonID').textContent = safeStr(analysis.numero_licitacion);
        document.getElementById('infoObjeto').textContent = safeStr(analysis.objeto);
        document.getElementById('infoFechaPub').textContent = safeStr(analysis.fecha_publicacion);

        // Licitante (asegurar carga de taxData si existe)
        document.getElementById('infoEmpresa').textContent = safeStr(tax.razon_social);
        document.getElementById('infoRFC').textContent = safeStr(tax.rfc);
        document.getElementById('infoDomicilio').textContent = safeStr(tax.domicilio_fiscal || tax.domicilio);

        // Cada vez que actualizamos la barra, refrescamos el studio si hay análisis
        if (activeNotebook.analysis) updateStudio(activeNotebook.analysis);
        if (activeNotebook.taxData || activeNotebook.logoFilename) updateEmpresaCard();

        updateIntelStatusList();
        // Cargar lista de documentos
        fetchGeneratedDocs();
    };

    const updateIntelStatusList = () => {
        if (!activeNotebook) return;
        const srcs = activeNotebook.sources || [];
        const analysis = activeNotebook.analysis;

        const checkPresence = (keywords) => {
            return srcs.some(s => {
                const fn = s.name.toLowerCase();
                return keywords.some(k => fn.includes(k));
            });
        };

        const setStatus = (id, isDone) => {
            const el = document.getElementById(id);
            if (!el) return;
            if (isDone) {
                el.classList.remove('pending');
                el.classList.add('done');
            } else {
                el.classList.add('pending');
                el.classList.remove('done');
            }
        };

        setStatus('statusBase', !!analysis);
        setStatus('statusActa', checkPresence(['acta', 'escritura', 'constitutiva']));
        setStatus('statusCIF', checkPresence(['cif', 'fiscal', 'constancia']) || !!activeNotebook.taxData);
        setStatus('statusLogo', checkPresence(['logo', 'firma', 'imagen']) || !!activeNotebook.logoFilename);
        setStatus('statusPrecios', checkPresence(['precios', 'catálogo', 'catalogo', 'costos', 'cotizacion', '.xlsx', '.xls']) || !!activeNotebook.excelProcessed);
    };

    const fetchGeneratedDocs = async () => {
        if (!activeNotebook) return;
        const listContainer = document.getElementById('generatedDocsList');
        const wsId = String(activeNotebook.id);

        try {
            const resp = await fetch(`http://localhost:8080/api/workspaces/${wsId}/files`);
            if (!resp.ok) return;
            const files = await resp.json();

            if (files.length === 0) {
                listContainer.innerHTML = '<p class="empty-msg-mini">Sin documentos aún</p>';
                return;
            }

            listContainer.innerHTML = '';
            files.sort((a, b) => b.name.localeCompare(a.name)).forEach(file => {
                // Solo mostrar los que no sean fuentes crudas (.pdf originales guardados)
                // O mejor aún, mostrar todos pero dar énfasis a DOCX
                if (file.name === 'analysis.json') return;

                const item = document.createElement('a');
                item.href = `http://localhost:8080/api/workspaces/${wsId}/download/${file.name}`;
                item.className = 'doc-item-mini';
                item.target = '_blank';
                item.title = `Descargar ${file.name}`;

                const icon = file.type === 'docx' ? '📝' : file.type === 'txt' ? '📄' : '📎';

                item.innerHTML = `
                    <span class="doc-icon">${icon}</span>
                    <span class="doc-name">${file.name}</span>
                    <span class="doc-download">↓</span>
                `;
                listContainer.appendChild(item);
            });
        } catch (e) {
            console.error("Error fetching docs", e);
        }
    };

    document.getElementById('btnRefreshDocs').onclick = (e) => {
        e.stopPropagation();
        fetchGeneratedDocs();
    };

    document.getElementById('btnGenerateDocs').onclick = async (e) => {
        e.stopPropagation();
        if (!activeNotebook) return;

        const wsId = String(activeNotebook.id);
        const btn = e.target;
        btn.disabled = true;
        btn.textContent = '✍️ Redactando...';

        // UI elements
        const progressArea = document.getElementById('genProgressArea');
        const progressBar = document.getElementById('genProgressBar');
        const statusText = document.getElementById('genStatusText');
        const docsList = document.getElementById('generatedDocsList');

        // Show progress area, reset bar
        progressArea.style.display = 'block';
        progressBar.style.width = '5%';
        statusText.textContent = 'Iniciando redacción...';
        docsList.innerHTML = '';

        let docsGenerated = [];
        let progress = 5;

        const advanceBar = (to, msg) => {
            progress = Math.max(progress, to);
            progressBar.style.width = progress + '%';
            if (msg) statusText.textContent = msg;
        };

        try {
            addMessage("✍️ Iniciando redacción de documentos legales...", 'bot');
            const resp = await fetch(`http://localhost:8080/api/generate-docs?workspace_id=${wsId}`, {
                method: 'POST'
            });

            if (!resp.ok) throw new Error("Error en generador (HTTP " + resp.status + ")");

            const reader = resp.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop();

                for (const line of lines) {
                    if (!line.trim()) continue;
                    const chunk = JSON.parse(line);

                    if (chunk.status === 'progress') {
                        const val = chunk.val || progress;
                        advanceBar(Math.min(val, 90), chunk.msg || statusText.textContent);

                    } else if (chunk.status === 'doc_ready') {
                        // Un documento terminó — mostrarlo en la lista inmediatamente
                        docsGenerated.push(chunk.name);
                        advanceBar(Math.min(progress + 5, 95), `Redactado: ${chunk.name}`);
                        addMessage(`📝 Generado: <strong>${chunk.name}</strong>`, 'bot');
                        docsList.innerHTML = docsGenerated.map(n =>
                            `<div class="doc-item"><span class="doc-icon">📝</span>
                             <a class="doc-name" href="http://localhost:8080/api/workspaces/${wsId}/download/${encodeURIComponent(chunk.folder_name + '/' + n)}" target="_blank">${n}</a>
                             <a class="doc-download" href="http://localhost:8080/api/workspaces/${wsId}/download/${encodeURIComponent(chunk.folder_name + '/' + n)}" download title="Descargar">↓</a></div>`
                        ).join('');

                    } else if (chunk.status === 'complete') {
                        advanceBar(100, '¡Documentos listos!');
                        progressBar.style.background = 'linear-gradient(90deg, #22c55e, #16a34a)';

                        const folderName = chunk.folder_name || 'documentos';
                        const zipUrl = `http://localhost:8080/api/workspaces/${wsId}/download-zip/${encodeURIComponent(folderName)}`;

                        addMessage(`✅ ${chunk.msg}`, 'bot');
                        addMessage(
                            `📂 <strong>Carpeta generada:</strong> <code style="font-size:11px; background:rgba(255,255,255,0.07); padding:2px 6px; border-radius:4px;">${folderName}</code><br>` +
                            `<a href="${zipUrl}" target="_blank" style="display:inline-block; margin-top:8px; padding:6px 14px; background:linear-gradient(135deg,var(--accent),#7c3aed); color:#fff; text-decoration:none; border-radius:8px; font-weight:600; font-size:13px;">📦 Descargar todos los documentos (.ZIP)</a>`,
                            'bot'
                        );
                        setTimeout(() => {
                            progressArea.style.display = 'none';
                        }, 3000);
                        fetchGeneratedDocs();

                    } else if (chunk.status === 'error') {
                        advanceBar(100, '❌ Error en la redacción');
                        progressBar.style.background = '#ef4444';
                        addMessage(`❌ Error: ${chunk.msg}`, 'bot');
                    }
                }
            }
        } catch (err) {
            console.error(err);
            statusText.textContent = '❌ Falló la conexión';
            progressBar.style.background = '#ef4444';
            progressBar.style.width = '100%';
            addMessage(`❌ No se pudieron generar los documentos: ${err.message}`, 'bot');
        } finally {
            btn.disabled = false;
            btn.textContent = 'Redactar';
        }
    };

    // --- Chat ---
    const chatInput = document.getElementById('chatInput');
    const btnSendMessage = document.getElementById('btnSendMessage');
    const chatMessages = document.getElementById('chatMessages');
    const btnClearChat = document.getElementById('btnClearChat');

    const addMessage = (text, type) => {
        const div = document.createElement('div');
        div.className = type === 'bot' ? 'bot-msg' : 'user-msg';
        div.innerHTML = `<p>${text}</p>`;
        chatMessages.appendChild(div);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    };

    const sendMessage = async () => {
        const text = chatInput.value.trim();
        if (!text || !activeNotebook) return;

        addMessage(text, 'user');
        chatInput.value = '';

        const typingDiv = document.createElement('div');
        typingDiv.className = 'bot-msg';
        typingDiv.innerHTML = '<p><em>El Gerente está consultando las bases...</em></p>';
        chatMessages.appendChild(typingDiv);
        chatMessages.scrollTop = chatMessages.scrollHeight;

        // Obtener lista de nombres de fuentes actuales para que el Chat sepa qué hay
        const sourceNames = activeNotebook.sources.map(s => s.name);

        try {
            const resp = await fetch('http://localhost:8080/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    workspace_id: String(activeNotebook.id),
                    question: text,
                    sources: sourceNames
                })
            });

            typingDiv.remove();

            if (resp.ok) {
                const data = await resp.json();
                addMessage(data.answer || "No obtuve respuesta.", 'bot');
            } else {
                addMessage("Lo siento, el Gerente no está disponible ahora mismo.", 'bot');
            }
        } catch (e) {
            typingDiv.remove();
            console.error("Chat error", e);
            addMessage("Error de conexión con el agente de chat.", 'bot');
        }
    };

    btnSendMessage.onclick = sendMessage;

    chatInput.onkeypress = (e) => {
        if (e.key === 'Enter') {
            sendMessage();
        }
    };
    if (btnClearChat) {
        btnClearChat.onclick = () => {
            chatMessages.innerHTML = '';
        };
    }

    // --- Reindexar ---
    document.getElementById('btnReindex').onclick = async () => {
        const btn = document.getElementById('btnReindex');
        btn.disabled = true;
        btn.textContent = 'Reindexando...';
        try {
            const resp = await fetch('http://localhost:8083/db/reindex', { method: 'POST' });
            const data = await resp.json();
            if (resp.ok) {
                alert(`✅ Reindexado completo: ${data.inserted} cuadernos recuperados.`);
                // Recargar vista
                notebooks = [];
                localStorage.removeItem('licitai_notebooks');
                await fetchNotebooks();
                loadNotebooks();
            } else {
                alert('❌ Error: ' + (data.detail || 'No se pudo reindexar'));
            }
        } catch (e) {
            alert('❌ Falló la conexión con memoria-db');
        } finally {
            btn.disabled = false;
            btn.textContent = '↻ Reindexar';
        }
    };

    // Inicialización
    fetchNotebooks();
    loadNotebooks();
});
