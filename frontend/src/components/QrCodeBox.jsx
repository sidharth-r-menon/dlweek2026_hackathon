import React from 'react'
import QRCode from 'react-qr-code'

export default function QrCodeBox({ value, size = 160 }) {
  return (
    <div className="flex flex-col items-center gap-2">
      <div style={{ background: '#1e293b', padding: 8, borderRadius: 12 }}>
        <QRCode value={value} size={size} fgColor="#0ea5e9" bgColor="#1e293b" />
      </div>
      <span className="text-xs text-slate-400 break-all mt-2">{value}</span>
    </div>
  )
}
