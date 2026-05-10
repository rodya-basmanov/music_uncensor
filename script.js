// 🎧 VK Playlist Exporter — Original Version
// Запускать в консоли на странице плейлиста: https://vk.com/music/playlist/...

(async () => {
    const delay = (ms) => new Promise((r) => setTimeout(r, ms));

    // 🔧 Очистка строки от feat., prod., remix и прочего
    function cleanTrack(text) {
        if (!text) return '';
        return text
            // Убираем всё в скобках, если там есть feat/prod/remix/live/explicit
            .replace(/\s*\([^)]*(?:feat\.?|ft\.?|featuring|prod\.?|produced by|remix|live|explicit|censor|radio edit)[^)]*\)\s*/gi, '')
            // Убираем feat./ft./featuring и всё после (до конца строки или до следующей скобки)
            .replace(/\s*(?:feat\.?|ft\.?|featuring)\s+[^,\n;|()]+/gi, '')
            // Убираем prod./produced by и всё после
            .replace(/\s*(?:prod\.?|produced by)\s+[^,\n;|()]+/gi, '')
            // Убираем "x", "&", "vs." между артистами (оставляем первого)
            .replace(/\s*(?:x|&|vs\.?|with)\s+[^,\n;|()]+/gi, '')
            // Убираем соавторов через запятую (оставляем первого артиста)
            .replace(/,\s+[^,]+$/g, '')
            // Убираем лишние пробелы, запятые, точки в конце
            .replace(/\s+/g, ' ')
            .replace(/[\s,\.]+$/, '')
            .trim();
    }

    // 1. Прокручиваем плейлист до конца
    async function loadFullPlaylist() {
        let lastHeight = 0;
        let sameCount = 0;
        console.log("🔄 Загружаю треки...");
        
        while (sameCount < 3) {
            window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'smooth' });
            await delay(1500);
            const newHeight = document.documentElement.scrollHeight;
            if (newHeight === lastHeight) {
                sameCount++;
            } else {
                sameCount = 0;
                lastHeight = newHeight;
            }
        }
        console.log("✅ Все треки загружены");
    }

    // Парсим длительность из текста "M:SS" или "MM:SS" → секунды
    function parseDuration(text) {
        if (!text) return 0;
        const m = text.match(/(\d+):(\d{2})/);
        if (m) return parseInt(m[1]) * 60 + parseInt(m[2]);
        return 0;
    }

    // Ищем duration в элементе и его соседях
    function findDuration(el) {
        // 1. data-duration атрибут
        let dur = el?.getAttribute?.('data-duration');
        if (dur && !isNaN(dur)) return parseInt(dur);

        // 2. Новый интерфейс — поднимаемся к корню трека и ищем duration
        if (el) {
            // Поднимаемся: Info → content → wrapper → overlay → cell → root
            const root = el.parentElement?.parentElement?.parentElement?.parentElement?.parentElement;
            if (root) {
                // Ищем элемент с классом vkitAudioPlayerPlaybackProgressTime__text*
                const durEl = root.querySelector('[class*="vkitAudioPlayerPlaybackProgressTime__text"]');
                if (durEl) {
                    const dur = parseDuration(durEl.textContent?.trim());
                    if (dur) return dur;
                }
            }
        }

        // 3. Старый интерфейс — .audio_row__duration
        if (el) {
            const row = el.closest?.('.audio_row');
            if (row) {
                const durEl = row.querySelector('.audio_row__duration');
                if (durEl) return parseDuration(durEl.textContent?.trim());
            }
        }

        return 0;
    }

    // 2. Парсим треки + чистим
    function parsePlaylist() {
        const tracks = [];
        
        // Новый интерфейс (2024+)
        const newRows = document.querySelectorAll('[data-testid="MusicTrackRow_Info"]');
        if (newRows.length > 0) {
            console.log(`🔍 Найдено треков (новый интерфейс): ${newRows.length}`);
            for (const row of newRows) {
                try {
                    let title = row.children[0]?.textContent?.trim() || '';
                    let artist = row.children[1]?.textContent?.trim() || '';
                    let duration = findDuration(row);
                    
                    if (artist || title) {
                        let line = `${artist} - ${title}`.replace(/\s+/g, ' ').trim();
                        if (duration) line += ` [${duration}]`;
                        tracks.push(line);
                    }
                } catch (e) { continue; }
            }
        } 
        // Старый интерфейс
        else {
            const oldRows = document.querySelectorAll('.audio_row__performer_title');
            console.log(`🔍 Найдено треков (старый интерфейс): ${oldRows.length}`);
            for (const row of oldRows) {
                try {
                    let artist = row.querySelector('.audio_row__performers')?.textContent?.trim() || '';
                    let title = row.querySelector('._audio_row__title a')?.textContent?.trim() || '';
                    let duration = findDuration(row);
                    
                    if (artist || title) {
                        let line = `${artist} - ${title}`.replace(/\s+/g, ' ').trim();
                        if (duration) line += ` [${duration}]`;
                        tracks.push(line);
                    }
                } catch (e) { continue; }
            }
        }
        return tracks.filter(t => t && t !== ' - ');
    }

    // 3. Получаем название плейлиста
    function getPlaylistTitle() {
        const newTitle = document.querySelector('.AudioPlaylistSnippet__title--main')?.textContent?.trim();
        if (newTitle) return newTitle;
        const oldTitle = document.querySelector('.ui_tab.ui_tab_sel')?.textContent?.trim();
        if (oldTitle) return oldTitle;
        return 'VK_Playlist';
    }

    // 4. Сохраняем файл
    function saveToFile(filename, content) {
        const data = content.replace(/\n/g, '\r\n');
        const blob = new Blob([data], { type: 'text/plain' });
        const link = document.createElement('a');
        link.download = filename;
        link.href = URL.createObjectURL(blob);
        link.style.display = 'none';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(link.href);
        console.log(`💾 Файл сохранён: ${filename}`);
    }

    // 5. Чистим имя файла
    function sanitizeFilename(name) {
        return name.replace(/[\\/:*?"<>|]/g, '_').slice(0, 100);
    }

    // === ЗАПУСК ===
    try {
        await loadFullPlaylist();
        const list = parsePlaylist();
        const rawTitle = getPlaylistTitle();
        const safeTitle = sanitizeFilename(rawTitle);
        
        if (list.length === 0) {
            console.warn("⚠️ Не найдено треков!");
            alert("⚠️ Не удалось найти треки. Убедись, что плейлист открыт.");
            return;
        }
        
        console.log(`🎵 Всего треков: ${list.length}`);
        const withDur = list.filter(t => t.includes('['));
        console.log(`⏱ С длительностью: ${withDur.length} / ${list.length}`);
        console.log("📋 Примеры:");
        list.slice(0, 5).forEach(t => console.log(`   • ${t}`));
        
        saveToFile(`${safeTitle}.txt`, list.join('\n'));
        alert(`✅ Готово! Экспортировано ${list.length} треков`);
        
    } catch (err) {
        console.error("❌ Ошибка:", err);
        alert(`❌ Ошибка: ${err.message}`);
    }
})();
