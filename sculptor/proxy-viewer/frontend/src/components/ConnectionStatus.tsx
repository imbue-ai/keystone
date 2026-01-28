import React from 'react';
import clsx from 'clsx';
import { ConnectionStatus as Status } from '../types';

interface ConnectionStatusProps {
  status: Status;
}

export function ConnectionStatus({ status }: ConnectionStatusProps) {
  const statusConfig = {
    connecting: { text: 'Connecting', color: 'bg-amber-400' },
    connected: { text: 'Connected', color: 'bg-emerald-400' },
    disconnected: { text: 'Disconnected', color: 'bg-gray-300' },
    error: { text: 'Error', color: 'bg-red-400' },
  };

  const config = statusConfig[status];

  return (
    <div className="flex items-center gap-2">
      <div className={clsx(
        'w-1.5 h-1.5 rounded-full',
        config.color
      )} />
      <span className="text-xs font-medium text-gray-600">{config.text}</span>
    </div>
  );
}
