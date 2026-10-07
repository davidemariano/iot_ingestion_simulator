import { HttpInterceptorFn } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';

export type Ruolo = 'admin' | 'agricoltore';

/**
 * Nel simulatore il ruolo sostituisce il JWT di Keycloak: registrazione, modifica ed
 * eliminazione restano visibili solo ad `admin` (§10.1, §12).
 */
@Injectable({ providedIn: 'root' })
export class RoleService {
  private readonly key = 'iotsim.ruolo';
  readonly ruolo = signal<Ruolo>(this.load());
  readonly isAdmin = computed(() => this.ruolo() === 'admin');

  set(r: Ruolo): void {
    this.ruolo.set(r);
    try {
      localStorage.setItem(this.key, r);
    } catch {
      /* storage non disponibile: il ruolo resta per la sessione */
    }
  }

  private load(): Ruolo {
    try {
      return localStorage.getItem(this.key) === 'agricoltore' ? 'agricoltore' : 'admin';
    } catch {
      return 'admin';
    }
  }
}

export const roleInterceptor: HttpInterceptorFn = (req, next) => {
  const role = inject(RoleService).ruolo();
  return next(req.clone({ setHeaders: { 'X-Ruolo': role } }));
};
