import React, { useState, useEffect, useCallback } from 'react';
import Sidebar from './components/Sidebar';
import Navbar from './components/Navbar';
import DashboardPage from './pages/DashboardPage';
import StudentsPage from './pages/StudentsPage';
import AcademicPerformancePage from './pages/AcademicPerformancePage';
import AttendancePage from './pages/AttendancePage';
import ReportsPage from './pages/ReportsPage';
import SystemHealthPage from './pages/SystemHealthPage';
import SettingsPage from './pages/SettingsPage';
import { apiService } from './services/api';

const TAB_TITLES = {
  dashboard: 'Executive Dashboard',
  students: 'Student Directory',
  academic: 'Academic Performance',
  attendance: 'Attendance Analytics',
  reports: 'Reports & Export',
  health: 'System Health & Probes',
  settings: 'Settings & Administration',
};

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard');
  const [overviewData, setOverviewData] = useState(null);
  const [healthStatus, setHealthStatus] = useState(null);
  const [loadingOverview, setLoadingOverview] = useState(true);
  const [overviewError, setOverviewError] = useState(null);
  const [isRefreshing, setIsRefreshing] = useState(false);

  const fetchGlobalData = useCallback(async () => {
    setIsRefreshing(true);
    try {
      const [overviewRes, healthRes] = await Promise.all([
        apiService.getOverview().catch((err) => {
          setOverviewError(err.message || 'Failed to fetch overview.');
          return null;
        }),
        apiService.getHealth().catch(() => null),
      ]);

      if (overviewRes) {
        setOverviewData(overviewRes);
        setOverviewError(null);
      }
      if (healthRes) {
        setHealthStatus(healthRes);
      }
    } finally {
      setLoadingOverview(false);
      setIsRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchGlobalData();
  }, [fetchGlobalData]);

  const renderActivePage = () => {
    switch (activeTab) {
      case 'dashboard':
        return (
          <DashboardPage
            overviewData={overviewData}
            loading={loadingOverview}
            error={overviewError}
            onNavigate={(tab) => setActiveTab(tab)}
          />
        );
      case 'students':
        return <StudentsPage onDataModified={fetchGlobalData} />;
      case 'academic':
        return <AcademicPerformancePage />;
      case 'attendance':
        return <AttendancePage />;
      case 'reports':
        return <ReportsPage />;
      case 'health':
        return <SystemHealthPage />;
      case 'settings':
        return <SettingsPage onDataReset={fetchGlobalData} />;
      default:
        return <DashboardPage overviewData={overviewData} loading={loadingOverview} error={overviewError} onNavigate={setActiveTab} />;
    }
  };

  return (
    <div className="flex min-h-screen bg-slate-50 font-sans">
      {/* Dark Navy Sidebar */}
      <Sidebar activeTab={activeTab} setActiveTab={setActiveTab} />

      {/* Main Content Workspace */}
      <div className="flex-1 flex flex-col min-w-0">
        <Navbar
          title={TAB_TITLES[activeTab] || 'EduTrack'}
          onRefresh={fetchGlobalData}
          isRefreshing={isRefreshing}
          healthStatus={healthStatus}
        />

        <main className="flex-1 overflow-y-auto">
          {renderActivePage()}
        </main>
      </div>
    </div>
  );
}
