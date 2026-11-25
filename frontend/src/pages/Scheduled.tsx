// Scheduled: autonomous content on a timetable. The page is in the design; the feature is the next build step,
// so this says plainly what it will do instead of showing fake schedules.
export default function Scheduled() {
  return (
    <section className="page">
      <div className="detail-header">
        <h1>Scheduled</h1>
        <span className="spacer" />
        <button className="btn btn-primary" disabled title="Coming in the next step">+ New schedule</button>
      </div>
      <p className="subtitle">Posts that build themselves on a schedule. You choose whether a person approves each one.</p>

      <div className="card soon">
        <h2 style={{ marginTop: 0 }}>Coming next</h2>
        <p>Each schedule will have:</p>
        <ul>
          <li><strong>Brand and platform</strong></li>
          <li><strong>What to post about</strong>: a fresh topic each time from a theme, or your own list of topics</li>
          <li><strong>When</strong>: once at a set time, or repeating (weekdays and a time, in India time)</li>
          <li><strong>Approval</strong>: a person approves each post, or fully automatic. Posts the reviewer flags always come to Reviews first.</li>
          <li><strong>Budget</strong>: a monthly cost cap, with an estimate before you save</li>
        </ul>
        <p className="muted small" style={{ marginTop: '0.8rem' }}>
          Builds started by a schedule will show up in Pipelines like any other.
        </p>
      </div>
    </section>
  )
}
