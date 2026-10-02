import DocumentsExplorer from "@/components/documents/DocumentsExplorer";

/** 문서 관리 - 공용 코퍼스(분류·폴더·감시 폴더). 관리자만 등록·수정. 사이드바 관리 묶음의 첫 줄. */
export default function DocumentsPage() {
  return <DocumentsExplorer scope="shared" />;
}
