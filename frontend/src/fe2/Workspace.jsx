import { NavLink } from 'react-router-dom';
import { Boxes, CreditCard, KeyRound, LayoutDashboard, Truck } from 'lucide-react';
import './workspace.css';
import OperationsPage from './OperationsPage';
import WarehousePage from './WarehousePage';
import PaymentsPage from './PaymentsPage';
import DashboardPage from './DashboardPage';

// Read access follows the existing backend role dependencies.
export const fe2Pages = [
  { path: 'van-tai', title: 'Vận tải', icon: Truck, roles: ['DSP', 'SAL', 'MGR'],
    description: 'Theo dõi phương tiện, tài xế và hành trình giao nhận.',
    sections: ['Xe và tài xế', 'Lịch và phân công', 'Nhận hàng, vận chuyển và giao hàng', 'Bằng chứng giao hàng', 'Sự cố và chuyến trễ'] },
  { path: 'thue-xe', title: 'Thuê xe', icon: KeyRound, roles: ['DSP', 'SAL', 'MGR'],
    description: 'Theo dõi xe cho thuê từ giữ lịch đến hoàn tất hồ sơ.',
    sections: ['Tìm xe trống', 'Giữ, đổi và hủy giữ xe', 'Bàn giao xe', 'Nhận lại xe', 'Hoàn tất hồ sơ'] },
  { path: 'kho', title: 'Kho', icon: Boxes, roles: ['WHS', 'DSP', 'SAL', 'MGR'],
    description: 'Quản lý lô hàng, tồn kho và chứng từ nhập xuất.',
    sections: ['Lô hàng và tồn kho', 'Phiếu nhập', 'Phiếu xuất', 'Lịch sử và liên kết vận tải', 'Điều chỉnh tồn kho', 'Đóng lô'] },
  { path: 'thanh-toan', title: 'Thanh toán', icon: CreditCard, roles: ['FIN', 'SAL', 'MGR'],
    description: 'Theo dõi công nợ và lịch sử thu tiền của đơn hàng.',
    sections: ['Danh sách công nợ', 'Chi tiết và lịch sử thanh toán', 'Ghi nhận thu tiền', 'Đảo khoản thu'] },
  { path: 'dashboard', title: 'Dashboard', icon: LayoutDashboard, roles: ['DSP', 'WHS', 'FIN', 'SAL', 'MGR'],
    description: 'Không gian tổng hợp vận hành và tài chính theo vai trò.',
    sections: ['Chỉ số theo vai trò và khoảng ngày', 'Danh sách chi tiết từ chỉ số', 'Xuất CSV theo bộ lọc'] },
];

export const canAccessFe2 = (me, page) => page.roles.some(role => (me.roles || []).includes(role));
export const fe2NavGroup = { label: 'VẬN HÀNH & TÀI CHÍNH', links: fe2Pages.map(page => [page.title, `/app/${page.path}`, page.icon, page.roles]) };

export function Fe2Page({ page, me, Empty }) {
  if (!canAccessFe2(me, page)) return <section className="panel fe2-page" role="alert">
    <h1>Không có quyền truy cập</h1>
    <p>Vai trò hiện tại của bạn không được xem khu vực {page.title}.</p>
    <NavLink className="btn btn-outline" to="/app">Về tổng quan</NavLink>
  </section>;

  if (page.path === 'van-tai' || page.path === 'thue-xe') return <OperationsPage key={page.path} rental={page.path === 'thue-xe'} me={me}/>;
  if (page.path === 'kho') return <WarehousePage me={me}/>;
  if (page.path === 'thanh-toan') return <PaymentsPage me={me}/>;
  if (page.path === 'dashboard') return <DashboardPage me={me}/>;
  const Icon = page.icon;
  return <div className="fe2-page">
    <div className="page-heading"><div><span className="eyebrow">VẬN HÀNH & TÀI CHÍNH</span><h1>{page.title}</h1><p>{page.description}</p></div><span className="metric-icon mint"><Icon size={24} aria-hidden="true" /></span></div>
    {page.path === 'dashboard' && <section className="panel fe2-links" aria-label="Truy cập khu vực nghiệp vụ">
      {fe2Pages.filter(item => item.path !== 'dashboard' && canAccessFe2(me, item)).map(item => <NavLink className="btn btn-outline" key={item.path} to={`/app/${item.path}`}><item.icon size={18} aria-hidden="true" />{item.title}</NavLink>)}
    </section>}
    <section className="panel">
      <div className="panel-heading"><div><h2>Khu vực chức năng</h2><p>Các chức năng đang được hoàn thiện.</p></div></div>
      <ul className="fe2-section-list">{page.sections.map(section => <li key={section}>{section}<span>Chưa triển khai</span></li>)}</ul>
      <Empty text="Trang đã sẵn sàng. Dữ liệu và thao tác nghiệp vụ sẽ được bổ sung trong các bước tiếp theo." />
    </section>
  </div>;
}
