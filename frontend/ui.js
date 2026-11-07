export const STATUS = {
  pending: ['Ожидает', 'clock'], planning: ['Подготовка плана', 'spark'],
  running: ['Выполняется', 'loader'], waiting_input: ['Требуется ответ', 'help'],
  done: ['Выполнено', 'check'], error: ['Ошибка', 'alert'],
  skipped: ['Пропущено', 'skip'], cancelled: ['Отменено', 'stop'],
  interrupted: ['Прервано', 'offline'], cancelling: ['Останавливается', 'loader'],
};

const ICONS = {
  plus: '<path d="M12 5v14M5 12h14"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13M10 11v5M14 11v5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  loader: '<path d="M20 12a8 8 0 1 1-2.34-5.66M18 3v4h-4"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.8 9a2.3 2.3 0 0 1 4.5.7c0 1.8-2.3 2-2.3 3.8M12 17h.01"/>',
  check: '<circle cx="12" cy="12" r="9"/><path d="m8 12 2.6 2.7L16 9.3"/>',
  alert: '<circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 17h.01"/>',
  skip: '<circle cx="12" cy="12" r="9"/><path d="M8.5 12h7"/>',
  stop: '<circle cx="12" cy="12" r="9"/><path d="M9 9h6v6H9z"/>',
  play: '<path d="m9 7 8 5-8 5V7Z"/>', chevron: '<path d="m9 10 3 3 3-3"/>',
  repeat: '<path d="M20 7h-9a6 6 0 1 0 5.2 9M17 4l3 3-3 3"/>',
  file: '<path d="M6 3h8l4 4v14H6V3ZM14 3v5h4M9 13h6M9 17h4"/>',
  x: '<path d="m7 7 10 10M17 7 7 17"/>',
  spark: '<path d="m12 3 1.2 4.1L17 9l-3.8 1.9L12 15l-1.2-4.1L7 9l3.8-1.9L12 3ZM18.5 15l.6 2.1L21 18l-1.9.9-.6 2.1-.6-2.1L16 18l1.9-.9.6-2.1Z"/>',
  copy: '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
  offline: '<path d="m3 3 18 18M8.5 8.5A12 12 0 0 1 21 9M3 9a12 12 0 0 1 2.3-1.5M6 12a8 8 0 0 1 3-1.6M11 14a4 4 0 0 1 4 1M12 19h.01"/>',
};

export function icon(name, size = 18) {
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ICONS.clock}</svg>`;
}

export function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]);
}

export function textValue(value) {
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2) ?? '';
}

export function statusPill(status) {
  const [label, name] = STATUS[status] || STATUS.pending;
  const style = STATUS[status] ? status : 'pending';
  return `<span class="status status--${style}">${icon(name, 14)}${label}</span>`;
}

export function duration(ms) {
  if (!Number.isFinite(ms)) return '—';
  return `${new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 1}).format(Math.max(0, ms) / 1000)} сек`;
}

export function dateLabel(timestamp) {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return 'Дата неизвестна';
  return new Intl.DateTimeFormat('ru-RU', {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'}).format(date);
}

const TOOL_TITLES = {
  read_file: 'Прочитать файл', write_file: 'Сохранить файл', list_dir: 'Получить список файлов',
  create_note: 'Сохранить заметку', get_note: 'Прочитать заметку', search_notes: 'Найти заметки', list_notes: 'Получить список заметок',
  create_pdf: 'Создать отчёт PDF', create_docx: 'Создать отчёт DOCX', create_pdf_with_table: 'Создать отчёт с таблицей',
  get_system_info: 'Получить сведения о системе', get_cpu: 'Получить сведения о процессоре',
  get_memory: 'Получить сведения о памяти', get_disk: 'Получить сведения о дисках', get_top_processes: 'Получить список процессов',
  send_email: 'Отправить письмо', send_email_with_attachment: 'Отправить письмо с вложением',
  search: 'Выполнить поиск в интернете', get_page_text: 'Прочитать веб-страницу', unavailable: 'Необходимый инструмент недоступен',
};

export function stepTitle(step) {
  if (step.description && /[а-яё]/i.test(step.description)) return step.description;
  return TOOL_TITLES[String(step.tool).split('.').pop()] || `Шаг ${step.id}`;
}

export function fieldLabel(key, title, description) {
  // q1/q2 are identifiers, not labels. Keep the actual question even if the
  // model ignored the requested response language instead of discarding it.
  const generic = /^(?:q(?:uestion)?[\s_-]*\d+|field[\s_-]*\d+|поле\s*[«"']?q\d+[»"']?)\??$/i;
  const labels = [title, description].filter(value => typeof value === 'string' && value.trim() && !generic.test(value.trim()));
  const russian = labels.find(value => /[а-яё]/i.test(value));
  if (russian) return russian;
  const question = labels.find(value => /[?\s]/.test(value) && !/^[\w.]+:\s*\w+$/.test(value));
  if (question) {
    const saving = /\b(?:sav(?:e|ed|ing)|writ(?:e|ten|ing)|stor(?:e|ed|ing)|output|destination)\b/i.test(question);
    if (/\b(?:text|content)\b/i.test(question) && saving) return 'Какой текст сохранить?';
    if (/\b(?:file|path|filename|location|directory|folder)\b/i.test(question) && saving) return 'Куда сохранить файл?';
    if (/\bfile\b/i.test(question) && /\bread\b/i.test(question)) return 'Какой файл прочитать?';
    if (/\b(?:recipient|to whom)\b/i.test(question)) return 'Кому отправить письмо?';
    return question;
  }
  const known = {
    path: 'Путь к файлу или каталогу', output_path: 'Путь для сохранения отчёта',
    content: 'Содержимое', title: 'Название', tags: 'Метки', query: 'Поисковый запрос',
    to: 'Получатель', subject: 'Тема письма', body: 'Текст письма', file_path: 'Путь к вложению',
    url: 'Адрес веб-страницы', max_results: 'Количество результатов', limit: 'Количество элементов',
    headers: 'Заголовки столбцов', rows: 'Строки таблицы',
  }[key];
  return known || labels[0] || (generic.test(key) ? 'Дополнительные данные' : `Поле «${key}»`);
}

const ERRORS = {
  not_found: 'Файл, каталог или другая запрошенная запись не найдены.',
  permission_denied: 'Недостаточно прав для доступа к файлу или каталогу.',
  invalid_path: 'Указан неподходящий путь к файлу или каталогу.',
  invalid_encoding: 'Файл не является текстом в кодировке UTF-8.',
  invalid_arguments: 'Параметры действия не соответствуют требованиям инструмента.',
  invalid_plan: 'Не удалось получить корректный план. Попробуйте уточнить задачу.',
  missing_result: 'Не получен результат необходимого предыдущего шага.',
  dependency_failed: 'Шаг пропущен: необходимый предыдущий шаг завершился с ошибкой.',
  tool_unavailable: 'Необходимый инструмент недоступен.',
  tool_error: 'Инструмент сообщил об ошибке выполнения.',
  rate_limited: 'Сервис модели ограничил запросы. Повторите попытку позже.',
  authentication_failed: 'Сервис отклонил авторизацию. Проверьте настройки ключа на сервере.',
  configuration_error: 'Для выполнения необходима настройка сервиса на сервере.',
  planning_timeout: 'Время ожидания плана истекло. Повторите попытку позже.',
  timeout: 'Время выполнения истекло. Результат внешнего действия может быть неизвестен.',
  connection_failed: 'Не удалось подключиться к внешнему сервису.',
  connection_lost: 'Соединение с запуском потеряно. Уже выполненные действия могли сохраниться.',
  cancelled: 'Выполнение остановлено. Уже выполненные действия сохраняются.',
  input_declined: 'Выполнение прекращено: пользователь отказался предоставить необходимые данные.',
  input_timeout: 'Время ожидания ответа истекло.',
  invalid_input: 'Проверьте заполнение обязательных полей и формат значений.',
  unknown_request: 'Запрос уточнения уже закрыт.',
  clarification_limit: 'Недостаточно данных после нескольких уточнений. Сформулируйте задачу подробнее.',
  extraction_failed: 'Не удалось извлечь содержимое веб-страницы.',
  storage_full: 'Недостаточно свободного места на диске.',
  storage_error: 'Ошибка хранилища заметок.',
  recipient_rejected: 'Почтовый сервис отклонил получателя.',
  sender_rejected: 'Почтовый сервис отклонил отправителя.',
  email_failed: 'Не удалось завершить отправку письма.',
  remote_error: 'Внешний сервис сообщил об ошибке.',
  planning_failed: 'Сервис модели не смог составить план.',
  io_error: 'Ошибка чтения или записи данных.',
  internal_error: 'Внутренняя ошибка. Подробности доступны в журнале сервера.',
  unsupported_result: 'Инструмент вернул неподдерживаемый формат результата.',
  unsupported_elicitation: 'Формат запрошенного уточнения не поддерживается.',
  invalid_elicitation: 'Инструмент запросил некорректную форму уточнения.',
  elicitation_required: 'Для этого действия необходимо предоставить дополнительные данные.',
};

export function errorText(code, original) {
  return ERRORS[code] || (original && /[а-яё]/i.test(original) ? original : 'Не удалось выполнить действие. Подробности доступны в журнале.');
}
