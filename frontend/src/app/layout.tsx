
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body style={{ margin: 0, background: "#0d1117", overflow: "hidden", fontFamily: "Arial", color: "white"}}>
        
        {children}
      </body>
    </html>
  );
}