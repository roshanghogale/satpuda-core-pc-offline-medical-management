import React, { useState, useEffect } from 'react'
import PurchasePage from './pages/PurchasePage.jsx'
import SupplierConfigPage from './pages/SupplierConfigPage.jsx'
import { loadRuntimeCatalog, getCatalogSnapshot } from './store.js'
import { loadBuiltInMedicines, getLoadedMedicineCount } from './medicineIndex.js'
import styles from './App.module.css'

export default function App() {
  const [page, setPage] = useState('purchase') // 'purchase' | 'config'
  const [connected, setConnected] = useState(false)
  const [medicineCount, setMedicineCount] = useState(0)
  const [catalog, setCatalog] = useState(() => getCatalogSnapshot())

  useEffect(() => {
    loadRuntimeCatalog().then(result => {
      setConnected(result.connected)
      setCatalog({
        suppliers: result.suppliers,
        schedules: result.schedules,
        medTypes: result.medTypes,
      })
    })
    loadBuiltInMedicines().then(() => setMedicineCount(getLoadedMedicineCount()))
  }, [])

  return (
    <div className={styles.app}>
      {!connected && (
        <div className={styles.connectBanner}>
          Open <strong>Settings → Open Web Purchase Entry</strong> from the desktop app (writes catalog.json with suppliers).
        </div>
      )}
      {connected && catalog.suppliers.length > 0 && (
        <div className={styles.connectBanner} style={{ background: '#ecfdf5', color: '#065f46', borderColor: '#6ee7b7' }}>
          Synced {catalog.suppliers.length} suppliers · {catalog.schedules.length} schedules · {catalog.medTypes.length} types
        </div>
      )}
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
        <span className={styles.hint}>
          {medicineCount > 0
            ? `${medicineCount.toLocaleString()} medicines (built-in) · `
            : ''}
          Ctrl+N = New Bill &nbsp;|&nbsp; Ctrl+S = Copy JSON
        </span>
      </nav>
      <main className={styles.main}>
        {page === 'purchase' ? (
          <PurchasePage
            suppliers={catalog.suppliers}
            schedules={catalog.schedules}
            medicineTypes={catalog.medTypes}
          />
        ) : (
          <SupplierConfigPage />
        )}
      </main>
    </div>
  )
}
