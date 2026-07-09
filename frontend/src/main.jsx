import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { AlertTriangle, ArrowLeft, CheckCircle2, Clock3, FileText, Printer, RefreshCw, Upload } from 'lucide-react';
import './styles.css';

const TERMINAL_STATES = new Set(['printed', 'manual_completed', 'cancelled', 'expired']);
const DEFAULT_UPLOAD_EXTENSIONS = ['.pdf', '.jpg', '.jpeg', '.png', '.tif', '.tiff', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.odt', '.ods', '.odp', '.txt', '.rtf', '.csv'];
const STATUS_META = {
  uploaded: { label: '已上传', tone: 'neutral' },
  queued_for_conversion: { label: '等待转换', tone: 'neutral' },
  converting: { label: '转换中', tone: 'neutral' },
  preview_ready: { label: '待确认', tone: 'warning' },
  waiting_user_confirm: { label: '待确认', tone: 'warning' },
  queued_for_print: { label: '等待打印', tone: 'neutral' },
  printing: { label: '打印中', tone: 'neutral' },
  submitted_to_cups: { label: '已提交', tone: 'neutral' },
  printed: { label: '已完成', tone: 'success' },
  conversion_failed: { label: '转换失败', tone: 'error' },
  print_failed: { label: '打印失败', tone: 'error' },
  print_status_unknown: { label: '状态未知', tone: 'error' },
  cancelled: { label: '已取消', tone: 'neutral' },
  manual_required: { label: '需人工处理', tone: 'warning' },
  manual_completed: { label: '人工完成', tone: 'success' },
  expired: { label: '已过期', tone: 'neutral' }
};
const EVENT_LABELS = {
  task_created: '任务创建',
  original_file_saved: '源文件保存',
  status_changed: '状态更新',
  task_leased: '任务领取',
  converted_pdf_file_saved: 'PDF 保存',
  preview_1_file_saved: '预览保存',
  conversion_finished: '转换完成',
  conversion_failed: '转换失败',
  preview_confirmed: '确认打印',
  cups_job_recorded: 'CUPS 提交',
  print_finished: '打印完成',
  print_failed: '打印失败',
  print_status_unknown: '状态未知',
  files_deleted: '文件清理'
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers: options.body instanceof FormData ? undefined : { 'Content-Type': 'application/json' },
    ...options
  });
  if (!response.ok) {
    let message = `请求失败：${response.status}`;
    try {
      const payload = await response.json();
      message = payload.detail || message;
    } catch {
      message = await response.text();
    }
    throw new Error(message);
  }
  return response.json();
}

function usePath() {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);
  const navigate = (nextPath) => {
    window.history.pushState({}, '', nextPath);
    setPath(nextPath);
  };
  return [path, navigate];
}

function App() {
  const [path, navigate] = usePath();
  const match = path.match(/^\/tasks\/(\d+)/);
  return (
    <Shell>
      {match ? <TaskDetail taskId={Number(match[1])} navigate={navigate} /> : <TaskList navigate={navigate} />}
    </Shell>
  );
}

function Shell({ children }) {
  return (
    <>
      <header className="topbar">
        <div>
          <h1>打印网关</h1>
          <p className="muted">网页上传，Linux 工作站 Agent 拉取任务并通过 CUPS 打印。</p>
        </div>
      </header>
      <main className="page">{children}</main>
    </>
  );
}

function TaskList({ navigate }) {
  const [tasks, setTasks] = useState([]);
  const [service, setService] = useState(null);
  const [error, setError] = useState('');
  const [fileError, setFileError] = useState('');
  const [busy, setBusy] = useState(false);

  const loadDashboard = async () => {
    const [servicePayload, tasksPayload] = await Promise.all([api('/api/service'), api('/api/tasks')]);
    setService(servicePayload);
    setTasks(tasksPayload.tasks);
  };

  useEffect(() => {
    loadDashboard().catch((err) => setError(err.message));
  }, []);

  const uploadExtensions = service?.allowed_uploads?.extensions || DEFAULT_UPLOAD_EXTENSIONS;
  const uploadAccept = uploadExtensions.join(',');

  const uploadFile = async (event) => {
    event.preventDefault();
    const file = event.currentTarget.file.files[0];
    if (!file) return;
    if (!isAllowedClientFile(file, uploadExtensions)) {
      setFileError('文件类型不支持');
      return;
    }
    setBusy(true);
    setError('');
    setFileError('');
    const formData = new FormData();
    formData.append('file', file);
    try {
      const payload = await api('/api/tasks/upload', { method: 'POST', body: formData });
      navigate(payload.location);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const handleFileChange = (event) => {
    const file = event.currentTarget.files[0];
    setFileError(file && !isAllowedClientFile(file, uploadExtensions) ? '文件类型不支持' : '');
  };

  if (!service && !error) {
    return <section className="panel full-width">加载服务状态</section>;
  }

  if (service && !service.available) {
    return <ServiceUnavailable error={error} onRefresh={() => loadDashboard().catch((err) => setError(err.message))} />;
  }

  return (
    <>
      <section className="panel upload-panel">
        <div className="section-title">
          <div>
            <h2>上传文件</h2>
            <p className="muted">服务可用，当前有 {service?.printer_count || 0} 台打印机。</p>
          </div>
          <StatusBadge status="printed" label="可用" />
        </div>
        {error && <div className="alert error">{error}</div>}
        {fileError && <div className="alert error">{fileError}</div>}
        <form className="upload-form" onSubmit={uploadFile}>
          <input type="file" name="file" accept={uploadAccept} onChange={handleFileChange} required />
          <button className="button primary" disabled={busy} type="submit">
            <Upload size={16} />
            {busy ? '上传中' : '上传'}
          </button>
        </form>
        <FileTypeList extensions={uploadExtensions} />
      </section>

      <section className="panel">
        <div className="section-title">
          <div>
            <h2>当前任务</h2>
            <p className="muted">{tasks.length} 个待处理任务</p>
          </div>
          <button className="icon-button" onClick={() => loadDashboard().catch((err) => setError(err.message))} aria-label="刷新任务">
            <RefreshCw size={18} />
          </button>
        </div>
        <DataTable
          columns={['任务', '状态', '打印机', '更新时间']}
          empty="暂无当前任务"
          rows={tasks.map((task) => [
            <TaskCell task={task} navigate={navigate} />,
            <StatusBadge status={task.status} />,
            task.printer_id || '-',
            formatTime(task.updated_at)
          ])}
        />
      </section>
    </>
  );
}

function TaskCell({ task, navigate }) {
  return (
    <div className="task-cell">
      <button className="link-button" onClick={() => navigate(`/tasks/${task.id}`)}>#{task.id}</button>
      <span>{task.source_filename}</span>
    </div>
  );
}

function ServiceUnavailable({ error, onRefresh }) {
  return (
    <section className="service-state full-width">
      <AlertTriangle size={20} />
      <div>
        <h2>服务不可用</h2>
        <p>未检测到可用打印机。</p>
        {error && <p className="muted">{error}</p>}
      </div>
      <button className="button secondary" onClick={onRefresh}>
        <RefreshCw size={16} />
        刷新
      </button>
    </section>
  );
}

function FileTypeList({ extensions }) {
  const visible = extensions.slice(0, 10);
  const remaining = extensions.length - visible.length;
  return (
    <div className="file-types" aria-label="可上传文件类型">
      {visible.map((extension) => (
        <span className="file-type" key={extension}>{extension}</span>
      ))}
      {remaining > 0 && <span className="file-type">+{remaining}</span>}
    </div>
  );
}

function TaskDetail({ taskId, navigate }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const loadDetail = async () => {
    const payload = await api(`/api/tasks/${taskId}`);
    setDetail(payload);
  };

  useEffect(() => {
    loadDetail().catch((err) => setError(err.message));
  }, [taskId]);

  const task = detail?.task;

  const cancelTask = async () => {
    setBusy(true);
    try {
      await api(`/api/tasks/${taskId}/cancel`, { method: 'POST', body: '{}' });
      await loadDetail();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  if (!detail) {
    return <section className="panel">加载任务中</section>;
  }

  return (
    <>
      <section className="panel full-width">
        <button className="button secondary" onClick={() => navigate('/')}>
          <ArrowLeft size={16} />
          返回
        </button>
      </section>

      {error && <div className="alert error full-width">{error}</div>}

      <section className="panel">
        <div className="section-title">
          <div>
            <h2>任务 #{task.id}</h2>
            <p className="muted">{task.source_filename}</p>
          </div>
          <StatusBadge status={task.status} />
        </div>
        <dl className="meta">
          <dt>MIME</dt>
          <dd>{task.source_mime}</dd>
          <dt>CUPS Job</dt>
          <dd>{task.cups_job_id || '-'}</dd>
          <dt>文件清理</dt>
          <dd>{task.files_deleted_at ? `已清理：${formatTime(task.files_deleted_at)}` : '未清理'}</dd>
          <dt>创建时间</dt>
          <dd>{formatTime(task.created_at)}</dd>
        </dl>
        <div className="button-row">
          {detail.converted_pdf_available && (
            <a className="button secondary" href={`/api/tasks/${task.id}/files/converted-pdf`}>
              <FileText size={16} />
              预览 PDF
            </a>
          )}
          {!TERMINAL_STATES.has(task.status) && (
            <button className="button secondary" disabled={busy} onClick={cancelTask}>取消任务</button>
          )}
        </div>
      </section>

      <ConfirmPanel detail={detail} taskId={taskId} onDone={loadDetail} />

      <EventTimeline events={detail.events} />
    </>
  );
}

function ConfirmPanel({ detail, taskId, onDone }) {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    printer_id: detail.printers[0]?.id || '',
    copies: 1,
    sides: 'one-sided',
    media: 'A4',
    orientation: 'portrait',
    page_ranges: '',
    color_mode: 'auto'
  });

  useEffect(() => {
    setForm((current) => ({ ...current, printer_id: detail.printers[0]?.id || current.printer_id }));
  }, [detail.printers]);

  const update = (key, value) => setForm((current) => ({ ...current, [key]: value }));

  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await api(`/api/tasks/${taskId}/confirm`, { method: 'POST', body: JSON.stringify(form) });
      await onDone();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel">
      <div className="section-title">
        <h2>确认打印</h2>
        <Printer size={20} />
      </div>
      {error && <div className="alert error">{error}</div>}
      {!detail.can_confirm && <p className="muted">当前状态不能确认打印。</p>}
      {detail.can_confirm && detail.printers.length === 0 && <div className="alert error">服务不可用：未检测到可用打印机。</div>}
      {detail.can_confirm && detail.printers.length > 0 && (
        <form className="form" onSubmit={submit}>
          <label>
            <span>打印机</span>
            <select value={form.printer_id} onChange={(event) => update('printer_id', event.target.value)} required>
              {detail.printers.map((printer) => (
                <option key={printer.id} value={printer.id}>{printer.display_name}</option>
              ))}
            </select>
          </label>
          <div className="form-grid">
            <label>
              <span>份数</span>
              <input type="number" min="1" max="99" value={form.copies} onChange={(event) => update('copies', Number(event.target.value))} />
            </label>
            <label>
              <span>单双面</span>
              <select value={form.sides} onChange={(event) => update('sides', event.target.value)}>
                <option value="one-sided">单面</option>
                <option value="two-sided-long-edge">双面长边</option>
                <option value="two-sided-short-edge">双面短边</option>
              </select>
            </label>
            <label>
              <span>纸张</span>
              <input value={form.media} onChange={(event) => update('media', event.target.value)} />
            </label>
            <label>
              <span>方向</span>
              <select value={form.orientation} onChange={(event) => update('orientation', event.target.value)}>
                <option value="portrait">纵向</option>
                <option value="landscape">横向</option>
              </select>
            </label>
            <label>
              <span>页码范围</span>
              <input placeholder="1-3,5" value={form.page_ranges} onChange={(event) => update('page_ranges', event.target.value)} />
            </label>
            <label>
              <span>颜色</span>
              <select value={form.color_mode} onChange={(event) => update('color_mode', event.target.value)}>
                <option value="auto">自动</option>
                <option value="color">彩色</option>
                <option value="monochrome">黑白</option>
              </select>
            </label>
          </div>
          <button className="button primary" disabled={busy} type="submit">确认并打印</button>
        </form>
      )}
    </section>
  );
}

function EventTimeline({ events }) {
  return (
    <section className="panel full-width">
      <div className="section-title">
        <div>
          <h2>事件日志</h2>
          <p className="muted">{events.length} 条记录</p>
        </div>
      </div>
      {events.length === 0 ? (
        <p className="empty">暂无事件</p>
      ) : (
        <ol className="event-timeline">
          {events.map((event) => {
            const status = event.status || 'uploaded';
            return (
              <li className="event-item" key={event.id}>
                <span className={`event-marker ${statusTone(status)}`} aria-hidden="true">
                  {eventIcon(status)}
                </span>
                <div className="event-body">
                  <div className="event-title">
                    <strong>{EVENT_LABELS[event.event_type] || event.event_type}</strong>
                    {event.status && <StatusBadge status={event.status} />}
                  </div>
                  <p className="event-meta">{formatTime(event.created_at)} · {event.actor}</p>
                  {event.message && <p className="event-message">{event.message}</p>}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

function DataTable({ columns, rows, empty }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr><td className="empty" colSpan={columns.length}>{empty}</td></tr>
          ) : rows.map((row, index) => (
            <tr key={index}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StatusBadge({ status, label }) {
  const meta = useMemo(() => STATUS_META[status] || { label: status, tone: 'neutral' }, [status]);
  return <span className={`badge ${meta.tone}`}>{label || meta.label}</span>;
}

function statusTone(status) {
  return (STATUS_META[status] || { tone: 'neutral' }).tone;
}

function eventIcon(status) {
  const tone = statusTone(status);
  if (tone === 'success') return <CheckCircle2 size={16} />;
  if (tone === 'error') return <AlertTriangle size={16} />;
  return <Clock3 size={16} />;
}

function isAllowedClientFile(file, extensions) {
  const lowerName = file.name.toLowerCase();
  return extensions.some((extension) => lowerName.endsWith(extension));
}

function formatTime(value) {
  if (!value) return '-';
  return value.replace('T', ' ').replace(/\.\d+\+00:00$/, ' UTC');
}

createRoot(document.getElementById('root')).render(<App />);
