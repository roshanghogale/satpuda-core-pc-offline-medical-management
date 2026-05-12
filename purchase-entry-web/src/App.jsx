import React, { useState } from 'react'
import PurchasePage from './pages/PurchasePage.jsx'
import SupplierConfigPage from './pages/SupplierConfigPage.jsx'
import styles from './App.module.css'

export default function App() {
  const [page, setPage] = useState('purchase') // 'purchase' | 'config'

  return (
    <div className={styles.app}>
      <nav className={styles.nav}>
        <span className={styles.brand}>📦 Purchase Entry</span>
        <div className={styles.navLinks}>
          <button
            className={page === 'purchase' ? styles.active : ''}
            onClick={() => setPage('purchase')}
          >
            Purchase Entry
          </button>
          <button
            className={page === 'config' ? styles.active : ''}
            onClick={() => setPage('config')}
          >
            ⚙ Settings
          </button>
        </div>
        <span className={styles.hint}>Ctrl+N = New Bill &nbsp;|&nbsp; Ctrl+S = Copy JSON</span>
      </nav>
      <main className={styles.main}>
        {page === 'purchase' ? <PurchasePage /> : <SupplierConfigPage />}
      </main>
    </div>
  )
}
