import type { ReactNode } from 'react';
import { Icon } from './Icon';

interface Props {
  title: string;
  actions?: ReactNode;
  onSearch?: (value: string) => void;
  searchPlaceholder?: string;
}

/** Title left, tools right, mirroring the reference dashboard's header. */
export function Topbar({
  title,
  actions,
  onSearch,
  searchPlaceholder = 'Search…',
}: Props) {
  return (
    <header className="topbar">
      <h1 className="topbar__title">{title}</h1>
      <span className="topbar__spacer" />

      {onSearch && (
        <label className="search">
          <Icon name="search" size={14} className="search__icon" />
          <input
            className="search__input"
            placeholder={searchPlaceholder}
            onChange={(e) => onSearch(e.target.value)}
          />
        </label>
      )}

      {actions}
    </header>
  );
}
