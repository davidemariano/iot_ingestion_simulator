import { Routes } from '@angular/router';

// Nella SPA di Agrivalor la sezione è /app/iot (§10.1); qui è la pagina principale.
export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'stazioni' },
  {
    path: 'stazioni',
    title: 'Stazioni · Agrivalor IoT',
    loadComponent: () =>
      import('./features/iot/pages/iot-dashboard/iot-dashboard.component').then(
        (m) => m.IotDashboardComponent,
      ),
  },
  {
    path: 'simulatore',
    title: 'Simulatore · Agrivalor IoT',
    loadComponent: () =>
      import('./features/simulator/pages/simulator-console/simulator-console.component').then(
        (m) => m.SimulatorConsoleComponent,
      ),
  },
  { path: '**', redirectTo: 'stazioni' },
];
